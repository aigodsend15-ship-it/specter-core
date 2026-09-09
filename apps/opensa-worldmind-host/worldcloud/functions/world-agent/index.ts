import { createClient } from 'jsr:@supabase/supabase-js@2';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL') ?? '';
const SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '';
const MAX_SKEW_SECONDS = 90;
const db = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { autoRefreshToken: false, persistSession: false } });

interface Proposal {
  version: 1;
  agentId: string;
  capability: string;
  kind: string;
  objectKey: string;
  parentRevisionId?: string | null;
  payload: unknown;
  payloadSha256: string;
  bounds?: unknown;
  provenance: { license: string; source: string; sha256: string; [key: string]: unknown };
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === 'GET') return json({ ok: true, service: 'specter-world-agent', version: 2 });
  if (req.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
  if (!SUPABASE_URL || !SERVICE_ROLE_KEY) return json({ error: 'server_not_configured' }, 503);

  try {
    const raw = new Uint8Array(await req.arrayBuffer());
    const proposal = JSON.parse(new TextDecoder().decode(raw)) as Proposal;
    const ts = req.headers.get('x-specter-ts') ?? '';
    const nonce = req.headers.get('x-specter-nonce') ?? '';
    const signature = (req.headers.get('x-specter-signature') ?? '').toLowerCase();
    const shapeError = validateShape(proposal, ts, nonce, signature);
    if (shapeError) return json({ error: shapeError }, 400);

    const now = Math.floor(Date.now() / 1000);
    const timestamp = Number(ts);
    if (!Number.isInteger(timestamp) || Math.abs(now - timestamp) > MAX_SKEW_SECONDS) return json({ error: 'stale_timestamp' }, 401);

    const { data: secretRow, error: secretError } = await db.from('world_secrets').select('value').eq('key', 'agent_hmac_master').single();
    if (secretError || !secretRow?.value) return json({ error: 'agent_auth_unavailable' }, 503);
    const hmacMaster = String(secretRow.value);

    const payloadSha256 = await sha256Hex(new TextEncoder().encode(canonicalJson(proposal.payload)));
    if (!timingSafeEqualHex(payloadSha256, proposal.payloadSha256.toLowerCase())) return json({ error: 'payload_hash_mismatch' }, 400);

    const bodySha256 = await sha256Hex(raw);
    const expectedSignature = await signAgent(hmacMaster, proposal.agentId, `${proposal.agentId}\n${ts}\n${nonce}\n${bodySha256}`);
    if (!timingSafeEqualHex(expectedSignature, signature)) return json({ error: 'bad_signature' }, 401);

    const { data: agent, error: agentError } = await db.from('world_agents').select('id,enabled,capabilities,budget').eq('id', proposal.agentId).maybeSingle();
    if (agentError) throw agentError;
    if (!agent?.enabled) return json({ error: 'agent_disabled_or_unknown' }, 403);
    if (!Array.isArray(agent.capabilities) || !agent.capabilities.includes(proposal.capability)) return json({ error: 'capability_denied' }, 403);

    const provenanceError = validateProvenance(proposal.provenance);
    if (provenanceError) return json({ error: provenanceError }, 400);

    const { error: nonceError } = await db.from('agent_nonces').insert({ agent_id: proposal.agentId, nonce });
    if (nonceError) {
      if (String(nonceError.code) === '23505') return json({ error: 'replay' }, 409);
      throw nonceError;
    }

    const { data: runtime, error: runtimeError } = await db.from('world_runtime').select('current_revision_id').eq('singleton', true).single();
    if (runtimeError) throw runtimeError;
    const current = runtime?.current_revision_id ?? null;
    if (proposal.parentRevisionId !== undefined && proposal.parentRevisionId !== current) return json({ error: 'stale_parent_revision', currentRevisionId: current }, 409);

    const { data: revision, error: revisionError } = await db.from('world_revisions').insert({
      parent_id: current,
      status: 'proposed',
      created_by: proposal.agentId,
      metadata: { capability: proposal.capability, kind: proposal.kind, objectKey: proposal.objectKey, budget: agent.budget ?? {} },
    }).select('id,revision_no,status,parent_id').single();
    if (revisionError) throw revisionError;

    const { error: mutationError } = await db.from('world_mutations').insert({
      revision_id: revision.id,
      agent_id: proposal.agentId,
      capability: proposal.capability,
      kind: proposal.kind,
      object_key: proposal.objectKey,
      payload: proposal.payload,
      bounds: proposal.bounds ?? null,
      provenance: proposal.provenance,
      payload_sha256: proposal.payloadSha256.toLowerCase(),
      accepted: false,
    });
    if (mutationError) {
      await db.from('world_revisions').update({ status: 'rejected', metadata: { reason: 'mutation_insert_failed' } }).eq('id', revision.id);
      throw mutationError;
    }

    await db.from('world_events').insert({ event_type: 'mutation_proposed', agent_id: proposal.agentId, revision_id: revision.id, payload: { capability: proposal.capability, kind: proposal.kind, objectKey: proposal.objectKey } });
    return json({ ok: true, revisionId: revision.id, revisionNo: revision.revision_no, parentRevisionId: revision.parent_id, status: revision.status, payloadSha256: proposal.payloadSha256.toLowerCase() }, 202);
  } catch (error) {
    console.error('world-agent proposal failed', error instanceof Error ? error.message : String(error));
    return json({ error: 'internal_error' }, 500);
  }
});

function validateShape(p: Proposal, ts: string, nonce: string, signature: string): string | null {
  if (!p || p.version !== 1) return 'invalid_version';
  if (!safeId(p.agentId, 64)) return 'invalid_agent_id';
  if (!safeId(p.capability, 96)) return 'invalid_capability';
  if (!safeId(p.kind, 96)) return 'invalid_kind';
  if (!safeObjectKey(p.objectKey)) return 'invalid_object_key';
  if (!/^[0-9a-f]{64}$/i.test(p.payloadSha256 ?? '')) return 'invalid_payload_sha256';
  if (!/^\d{10,13}$/.test(ts)) return 'invalid_timestamp';
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(nonce)) return 'invalid_nonce';
  if (!/^[0-9a-f]{64}$/i.test(signature)) return 'invalid_signature';
  return null;
}

function validateProvenance(p: Proposal['provenance']): string | null {
  if (!p || typeof p !== 'object') return 'missing_provenance';
  if (typeof p.license !== 'string' || p.license.length < 2 || p.license.length > 128) return 'invalid_license';
  if (typeof p.source !== 'string' || p.source.length < 2 || p.source.length > 2048) return 'invalid_source';
  if (!/^[0-9a-f]{64}$/i.test(p.sha256 ?? '')) return 'invalid_provenance_sha256';
  return null;
}
function safeId(value: unknown, max: number): value is string { return typeof value === 'string' && value.length > 0 && value.length <= max && /^[A-Za-z0-9._:-]+$/.test(value); }
function safeObjectKey(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 256 && /^[A-Za-z0-9._:/-]+$/.test(value); }
function canonicalJson(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') { if (!Number.isFinite(value)) throw new Error('non_finite_number'); return JSON.stringify(value); }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') { const r = value as Record<string, unknown>; return `{${Object.keys(r).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(r[k])}`).join(',')}}`; }
  throw new Error('unsupported_payload_type');
}
async function sha256Hex(bytes: Uint8Array): Promise<string> { const owned = Uint8Array.from(bytes); const digest = await crypto.subtle.digest('SHA-256', owned.buffer); return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join(''); }
async function signAgent(masterSecret: string, agentId: string, message: string): Promise<string> {
  const enc = new TextEncoder();
  const master = await crypto.subtle.importKey('raw', enc.encode(masterSecret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const derivedRaw = await crypto.subtle.sign('HMAC', master, enc.encode(agentId));
  const derived = await crypto.subtle.importKey('raw', derivedRaw, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', derived, enc.encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
function timingSafeEqualHex(a: string, b: string): boolean { if (a.length !== b.length) return false; let diff = 0; for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i); return diff === 0; }
function json(body: unknown, status = 200): Response { return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } }); }
