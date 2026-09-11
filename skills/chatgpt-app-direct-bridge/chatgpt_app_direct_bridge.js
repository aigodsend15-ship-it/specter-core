const net = require('net');
const crypto = require('crypto');
const fs = require('fs');
const { execSync } = require('child_process');

const RECEIPTS = process.env.SPECTER_DIRECT_RECEIPTS ||
  './chatgpt_direct_receipts.jsonl';

function sha(v) {
  return crypto.createHash('sha256')
    .update(typeof v === 'string' ? v : JSON.stringify(v))
    .digest('hex');
}

function discoverPipe() {
  if (process.env.CODEX_APP_TOOLS_PIPE_PATH) return process.env.CODEX_APP_TOOLS_PIPE_PATH;
  const out = execSync(
    'wmic process where "name=\'codex.exe\'" get CommandLine /value',
    { encoding: 'utf8' }
  );
  const m = out.match(/codex-browser-use-[0-9a-f-]+/i);
  if (!m) throw new Error('Codex App Tools pipe not found');
  return `\\\\.\\pipe\\${m[0]}`;
}

function rpc(pipe, method, params) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(pipe);
    let buf = Buffer.alloc(0);
    socket.once('error', reject);
    socket.once('connect', () => {
      const payload = Buffer.from(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }));
      const frame = Buffer.alloc(4 + payload.length);
      frame.writeUInt32LE(payload.length, 0);
      payload.copy(frame, 4);
      socket.write(frame);
    });
    socket.on('data', chunk => {
      buf = Buffer.concat([buf, chunk]);
      if (buf.length < 4) return;
      const n = buf.readUInt32LE(0);
      if (buf.length < n + 4) return;
      const msg = JSON.parse(buf.subarray(4, n + 4).toString('utf8'));
      socket.end();
      if (msg.error) reject(new Error(`${msg.error.code}: ${msg.error.message}`));
      else resolve(msg.result);
    });
  });
}

function parseContent(result) {
  const text = (result?.contentItems || []).find(x => x.type === 'inputText')?.text;
  if (!text) return result;
  try { return JSON.parse(text); } catch { return text; }
}

function writeReceipt(sourceThreadId, tool, args, result, success) {
  const row = {
    receipt_id: 'chat-direct-' + crypto.randomUUID(),
    ts: new Date().toISOString(),
    source_thread_id: sourceThreadId,
    target_thread_id: args.threadId || null,
    tool,
    args_sha256: sha(args),
    result_sha256: sha(result),
    success: !!success
  };
  fs.appendFileSync(RECEIPTS, JSON.stringify(row) + '\n');
  return row;
}

async function callTool(sourceThreadId, tool, args) {
  if (tool === 'send_message_to_thread' && process.env.SPECTER_OWNER_APPROVED !== '1') {
    throw new Error('send requires SPECTER_OWNER_APPROVED=1');
  }
  const params = {
    arguments: args,
    callId: 'specter-' + crypto.randomUUID(),
    namespace: 'codex_app',
    threadId: sourceThreadId,
    tool,
    turnId: 'specter-' + crypto.randomUUID()
  };
  try {
    const raw = await rpc(discoverPipe(), 'tools/call', params);
    const parsed = parseContent(raw);
    return {
      result: parsed,
      receipt: writeReceipt(sourceThreadId, tool, args, parsed, raw?.success === true)
    };
  } catch (e) {
    e.receipt = writeReceipt(sourceThreadId, tool, args, { error: e.message }, false);
    throw e;
  }
}

async function main() {
  const [cmd, source, target, ...rest] = process.argv.slice(2);
  if (!cmd || !source) throw new Error('usage: list|read|send <sourceThreadId> [targetThreadId] [message]');
  let tool, args;
  if (cmd === 'list') {
    tool = 'list_threads';
    args = { limit: Number(target || 20) };
  } else if (cmd === 'read') {
    tool = 'read_thread';
    args = { threadId: target, turnLimit: 5, includeOutputs: false, maxOutputCharsPerItem: 12000 };
  } else if (cmd === 'send') {
    tool = 'send_message_to_thread';
    args = { threadId: target, prompt: rest.join(' ') };
  } else {
    throw new Error('unknown command');
  }
  const out = await callTool(source, tool, args);
  console.log(JSON.stringify(out, null, 2));
}

main().catch(e => {
  console.error(JSON.stringify({ ok: false, error: e.message, receipt: e.receipt || null }, null, 2));
  process.exitCode = 1;
});
