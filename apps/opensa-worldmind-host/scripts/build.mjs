import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const hostRoot = resolve(here, '..');
const work = join(hostRoot, '.opensa');
const sourceRepo = process.env.OPENSA_REPO ?? 'https://github.com/AlexSergey/opensa-fork-notice.git';
const sourceCommit = process.env.OPENSA_COMMIT ?? '2ba79d93d7bb08fd59aed298310e58870b004d38';

function run(command, args, cwd = hostRoot) {
  const result = spawnSync(command, args, {
    cwd,
    env: process.env,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed with ${result.status}`);
}

function replaceOnce(text, needle, replacement, label) {
  const at = text.indexOf(needle);
  if (at < 0) throw new Error(`OpenSA patch anchor not found: ${label}`);
  if (text.indexOf(needle, at + needle.length) >= 0) throw new Error(`OpenSA patch anchor is ambiguous: ${label}`);
  return text.slice(0, at) + replacement + text.slice(at + needle.length);
}

rmSync(work, { recursive: true, force: true });
run('git', ['clone', '--filter=blob:none', '--no-checkout', sourceRepo, work]);
run('git', ['checkout', '--detach', sourceCommit], work);

const bridgeSource = join(hostRoot, 'patches', 'worldmind-agent-build.ts');
const bridgeTarget = join(work, 'apps', 'web', 'src', 'ui', 'worldmind-agent-build.ts');
mkdirSync(dirname(bridgeTarget), { recursive: true });
cpSync(bridgeSource, bridgeTarget);

// OpenSA intentionally targets a pre-ES2022 library. Keep the injected bridge compatible
// instead of widening the entire engine's JS target for one Array.prototype.at call.
let bridge = readFileSync(bridgeTarget, 'utf8');
bridge = replaceOnce(
  bridge,
  '    const record = records.at(-1);',
  '    const record = records.length > 0 ? records[records.length - 1] : undefined;',
  'worldmind ES target compatibility',
);
writeFileSync(bridgeTarget, bridge);

const hostFile = join(work, 'apps', 'web', 'src', 'ui', 'engine-canvas-host.tsx');
let host = readFileSync(hostFile, 'utf8');
host = replaceOnce(
  host,
  "import { setupEngineProps } from './engine-props';",
  "import { setupEngineProps } from './engine-props';\nimport { setupWorldMindAgents } from './worldmind-agent-build';",
  'worldmind import',
);
host = replaceOnce(
  host,
  '  const props = setupEngineProps(engine, fs, physics);',
  "  const props = setupEngineProps(engine, fs, physics);\n  // SPECTER WorldMind: capability-gated autonomous builders over the real OpenSA/GTA world.\n  const worldMind = setupWorldMindAgents({ adapter, engine, fs, playerPosition: viewOf });",
  'worldmind setup',
);
host = replaceOnce(
  host,
  '      props.update();\n      propsMs = performance.now() - propsStarted;',
  '      props.update();\n      worldMind.update();\n      propsMs = performance.now() - propsStarted;',
  'worldmind frame update',
);
writeFileSync(hostFile, host);

const sourceSha = createHash('sha256').update(readFileSync(bridgeSource)).digest('hex');
const injectedSha = createHash('sha256').update(bridge).digest('hex');
const patchedSha = createHash('sha256').update(host).digest('hex');
console.log(JSON.stringify({ event: 'worldmind_patch', sourceCommit, bridgeSourceSha256: sourceSha, bridgeInjectedSha256: injectedSha, hostSha256: patchedSha }));

run('npm', ['ci'], work);
run('npm', ['run', 'build:prod'], work);

const dist = join(work, 'dist');
if (!existsSync(dist)) throw new Error(`OpenSA build finished without dist at ${dist}`);
console.log(JSON.stringify({ event: 'worldmind_build_ok', sourceCommit, dist }));
