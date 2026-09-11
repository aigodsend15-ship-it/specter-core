const { execFileSync } = require('child_process');
const path = require('path');

const bridge = path.join(__dirname, 'chatgpt_app_direct_bridge.js');
const source = process.env.SPECTER_SOURCE_THREAD;
const agentA = process.env.SPECTER_AGENT_A;
const agentB = process.env.SPECTER_AGENT_B;
const maxRounds = Math.min(Number(process.env.SPECTER_MAX_ROUNDS || 3), 3);
const mission = process.argv.slice(2).join(' ');

if (!source || !agentA || !agentB || !mission) {
  throw new Error('Set SPECTER_SOURCE_THREAD, SPECTER_AGENT_A, SPECTER_AGENT_B and provide a mission.');
}

function run(args, mutating = false) {
  const env = { ...process.env };
  if (mutating) env.SPECTER_OWNER_APPROVED = '1';
  const out = execFileSync(process.execPath, [bridge, ...args], { encoding: 'utf8', env });
  return JSON.parse(out);
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function latestAgentText(threadId) {
  for (let i = 0; i < 12; i++) {
    const r = run(['read', source, threadId]).result;
    const turns = r.turns || [];
    for (const turn of turns) {
      const msg = (turn.items || []).find(x => x.type === 'agentMessage' && x.text);
      if (msg) return msg.text;
    }
    sleep(1000);
  }
  throw new Error(`No agent response from ${threadId}`);
}

function marker(text, name) {
  const re = new RegExp(`${name}:\\s*([\\s\\S]*)$`, 'i');
  return text.match(re)?.[1]?.trim() || text;
}

let toA = `[PAIR SESSION] ${mission}\nAct as Agent A (architect). End with TO_AGENT_B: and a concise handoff.`;
for (let round = 1; round <= maxRounds; round++) {
  run(['send', source, agentA, toA], true);
  const aText = latestAgentText(agentA);
  const toB = marker(aText, 'TO_AGENT_B');

  run(['send', source, agentB,
    `[PAIR SESSION round ${round}] Agent A handoff:\n${toB}\nAct as Agent B (verifier/planner). Challenge it, assign concrete tasks, and end with TO_AGENT_A:`], true);
  const bText = latestAgentText(agentB);
  toA = `[PAIR SESSION round ${round}] Agent B response:\n${marker(bText, 'TO_AGENT_A')}\nRefine the plan. Do not execute changes. End with TO_AGENT_B:`;
}

console.log(JSON.stringify({ ok: true, rounds: maxRounds, agentA, agentB }, null, 2));
