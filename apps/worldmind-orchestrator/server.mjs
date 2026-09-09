import http from 'node:http';
import crypto from 'node:crypto';

const PORT = Number(process.env.PORT || 10000);
const EDGE_URL = process.env.WORLDMIND_EDGE_URL || '';
const HMAC_MASTER = process.env.WORLDMIND_HMAC_SECRET || '';
const TICK_MS = Math.max(60_000, Number(process.env.WORLDMIND_TICK_MS || 60_000));
const MODEL = process.env.WORLDMIND_MODEL || 'deterministic-planner';

const AGENTS = [
  { id: 'forge', role: 'builder', capability: 'world.build' },
  { id: 'aether', role: 'planner', capability: 'world.create_project' },
  { id: 'nyx', role: 'scout', capability: 'world.inspect' },
  { id: 'helios', role: 'orchestrator', capability: 'world.create_project' },
];
const BUILD_KEYWORDS = ['bench', 'lamp', 'tree', 'plant', 'fence', 'crate', 'barrel', 'table'];
const GANTON = [2495, -1675, 15.1];

let sequence = 0;
let running = false;
let lastTick = null;
let lastError = null;
let accepted = 0;
let rejected = 0;
let promoted = 0;

const canonical = (value) => {
  if (value === null) return 'null';
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('non_finite_number');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  throw new Error('unsupported_value');
};
const sha256 = (text) => crypto.createHash('sha256').update(text).digest('hex');
const hmac = (key, text) => crypto.createHmac('sha256', key).update(text).digest();

function sign(agentId, raw, ts, nonce) {
  const derived = hmac(HMAC_MASTER, agentId);
  const bodyHash = crypto.createHash('sha256').update(raw).digest('hex');
  return crypto.createHmac('sha256', derived).update(`${agentId}\n${ts}\n${nonce}\n${bodyHash}`).digest('hex');
}

function forgePayload(n) {
  const buildNo = Math.floor((n - 1) / AGENTS.length);
  const angle = (buildNo * 2.399963229728653) % (Math.PI * 2);
  const ring = 18 + (buildNo % 4) * 7;
  const assetKeyword = BUILD_KEYWORDS[buildNo % BUILD_KEYWORDS.length];
  return {
    assetKeyword,
    position: [
      Number((GANTON[0] + Math.cos(angle) * ring).toFixed(3)),
      Number((GANTON[1] + Math.sin(angle) * ring).toFixed(3)),
      GANTON[2],
    ],
    heading: Number((angle + Math.PI * 0.5).toFixed(6)),
    purpose: `WorldMind ${assetKeyword} improvement around the Ganton baseline district`,
    model: MODEL,
    constraints: { preserveBase: true, decorative: true, noNavCut: true, maxObjects: 1, rollback: true },
  };
}

function makeProposal(agent, n) {
  const construction = agent.id === 'forge';
  const payload = construction ? forgePayload(n) : {
    model: MODEL,
    role: agent.role,
    sequence: n,
    objective: agent.id === 'aether'
      ? 'plan a performance-safe RP district improvement using existing GTA systems'
      : agent.id === 'nyx'
        ? 'inspect navigation, collision and interaction opportunities in the current GTA district'
        : 'coordinate the next small reversible world improvement and validation pass',
    constraints: { preserveBase: true, requireCollisionGate: true, requireNavGate: true, requireRollback: true },
  };
  const payloadSha256 = sha256(canonical(payload));
  return {
    version: 1,
    agentId: agent.id,
    capability: agent.capability,
    kind: construction ? 'construction_project' : agent.id === 'nyx' ? 'world_survey' : 'improvement_plan',
    objectKey: `worldmind/${agent.id}/${String(n).padStart(10, '0')}`,
    payload,
    payloadSha256,
    provenance: {
      license: 'SPECTER-WORLD-OVERLAY',
      source: 'specter-worldmind-orchestrator',
      sha256: sha256(`specter-worldmind-orchestrator:${agent.id}:${n}`),
    },
  };
}

async function submit(agent, n) {
  const proposal = makeProposal(agent, n);
  const raw = Buffer.from(JSON.stringify(proposal));
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = `${agent.id}:${Date.now()}:${crypto.randomBytes(12).toString('hex')}`;
  const response = await fetch(EDGE_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-specter-ts': ts,
      'x-specter-nonce': nonce,
      'x-specter-signature': sign(agent.id, raw, ts, nonce),
    },
    body: raw,
  });
  const text = await response.text();
  if (!response.ok) {
    rejected += 1;
    throw new Error(`${agent.id}:${response.status}:${text.slice(0, 300)}`);
  }
  accepted += 1;
  try {
    const parsed = JSON.parse(text);
    if (parsed.status === 'promoted') promoted += 1;
  } catch { /* response is diagnostic only */ }
  return text;
}

async function tick() {
  if (running || !EDGE_URL || !HMAC_MASTER) return;
  running = true;
  try {
    const agent = AGENTS[sequence % AGENTS.length];
    sequence += 1;
    const result = await submit(agent, sequence);
    lastTick = new Date().toISOString();
    lastError = null;
    console.log(JSON.stringify({ event: 'worldmind_tick_ok', agent: agent.id, sequence, result: result.slice(0, 400) }));
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    console.error(JSON.stringify({ event: 'worldmind_tick_error', error: lastError }));
  } finally {
    running = false;
  }
}

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    res.end(JSON.stringify({ ok: Boolean(EDGE_URL && HMAC_MASTER), service: 'specter-worldmind-orchestrator', model: MODEL, agents: AGENTS.length, sequence, accepted, rejected, promoted, lastTick, lastError }));
    return;
  }
  if (req.url === '/tick' && req.method === 'POST') {
    void tick();
    res.writeHead(202, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true, scheduled: true }));
    return;
  }
  res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' });
  res.end('SPECTER WorldMind Orchestrator\n');
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(JSON.stringify({ event: 'worldmind_started', port: PORT, agents: AGENTS.map((a) => a.id), tickMs: TICK_MS, model: MODEL }));
  setTimeout(() => void tick(), 5_000);
  setInterval(() => void tick(), TICK_MS).unref();
});
