import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const hostRoot = resolve(here, '..');
const work = join(hostRoot, '.opensa');
const legacyWork = join(hostRoot, '.opensa-legacy');
const sourceRepo = process.env.OPENSA_REPO ?? 'https://github.com/AlexSergey/opensa-fork-notice.git';
const sourceCommit = process.env.OPENSA_COMMIT ?? '2ba79d93d7bb08fd59aed298310e58870b004d38';
// Last commit before OpenSA deleted its Three/WebGL renderer (074/13 phase 5).
const legacyCommit = process.env.OPENSA_LEGACY_COMMIT ?? 'cacc1f0b8332d3d96490e593f95c02e2ab23b1a7';
const renderHost = process.env.OPENSA_ALLOWED_HOST ?? 'specter-gtasa-worldmind.onrender.com';

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

function cloneAt(commit, destination) {
  rmSync(destination, { recursive: true, force: true });
  run('git', ['clone', '--filter=blob:none', '--no-checkout', sourceRepo, destination]);
  run('git', ['checkout', '--detach', commit], destination);
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

// ---------------------------------------------------------------------------
// Modern WebGPU build: WorldMind + Core -> Compatibility graphics ladder.
// ---------------------------------------------------------------------------
cloneAt(sourceCommit, work);

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

// Replace the upstream one-shot WebGPU probe with a strict site-side ladder:
// Core -> Compatibility feature level -> WebGL2 legacy renderer.
const graphicsGateSource = join(hostRoot, 'patches', 'webgpu-gate.ts');
const graphicsGateTarget = join(work, 'apps', 'web', 'src', 'ui', 'shell', 'webgpu-gate.ts');
cpSync(graphicsGateSource, graphicsGateTarget);

// The shell redirects transparently to the bundled Three/WebGL2 build only after both WebGPU asks fail.
const appFile = join(work, 'apps', 'web', 'src', 'ui', 'shell', 'app.tsx');
let app = readFileSync(appFile, 'utf8');
app = replaceOnce(
  app,
  `    void probeWebGpu().then((probe) => {\n      if (!cancelled) {\n        setWebGpu(probe);\n      }\n    });`,
  `    void probeWebGpu().then((probe) => {\n      if (cancelled) {\n        return;\n      }\n      if (probe === 'webgl2') {\n        const target = new URL('/legacy/', window.location.origin);\n        const params = new URLSearchParams(window.location.search);\n        params.set('engine', 'three');\n        params.set('fallback', 'webgl2');\n        target.search = params.toString();\n        window.location.replace(target.toString());\n\n        return;\n      }\n      setWebGpu(probe);\n    });`,
  'WebGL2 fallback redirect',
);
app = replaceOnce(
  app,
  `  if (webGpu !== null && webGpu !== 'ok') {`,
  `  if (webGpu !== null && webGpu !== 'ok' && webGpu !== 'compatibility' && webGpu !== 'webgl2') {`,
  'compatibility/WebGL2 states accepted by shell',
);
writeFileSync(appFile, app);

// Engine device creation must use the SAME ladder as the shell probe. Otherwise the shell can admit an
// Intel compatibility adapter and the engine immediately re-request core and fail again.
const deviceFile = join(work, 'packages', 'engine', 'src', 'core', 'device.ts');
let device = readFileSync(deviceFile, 'utf8');
device = replaceOnce(
  device,
  `  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });\n  if (!adapter) {\n    throw new Error('WebGPU adapter request failed (blocklisted GPU or disabled flag?)');\n  }`,
  `  type AdapterOptionsWithLevel = GPURequestAdapterOptions & { featureLevel?: 'compatibility' | 'core' };\n  const requestLevel = async (\n    featureLevel: 'compatibility' | 'core',\n    powerPreference?: GPUPowerPreference,\n  ): Promise<GPUAdapter | null> => {\n    try {\n      const options: AdapterOptionsWithLevel = {\n        featureLevel,\n        ...(powerPreference ? { powerPreference } : {}),\n      };\n\n      return await navigator.gpu.requestAdapter(options);\n    } catch {\n      return null;\n    }\n  };\n\n  const adapter =\n    (await requestLevel('core', 'high-performance')) ??\n    (await requestLevel('compatibility'));\n  if (!adapter) {\n    throw new Error('WebGPU adapter request failed in both core and compatibility modes');\n  }`,
  'engine WebGPU core/compatibility ladder',
);
writeFileSync(deviceFile, device);

// Vite 8 rejects unknown Host headers by default. Render forwards the public .onrender.com hostname,
// so explicitly allow only that deployment hostname rather than disabling host checks.
const viteFile = join(work, 'vite.config.ts');
let vite = readFileSync(viteFile, 'utf8');
vite = replaceOnce(
  vite,
  'export default defineConfig(({ command }) => ({\n  build: {',
  `export default defineConfig(({ command }) => ({\n  server: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  preview: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  build: {`,
  'Render Vite allowed host',
);
writeFileSync(viteFile, vite);

console.log(
  JSON.stringify({
    event: 'graphics_ladder_patch',
    sourceCommit,
    legacyCommit,
    renderHost,
    worldMindSha256: sha256(bridge),
    graphicsGateSha256: sha256(readFileSync(graphicsGateTarget)),
    appSha256: sha256(app),
    deviceSha256: sha256(device),
    viteSha256: sha256(vite),
  }),
);

run('npm', ['ci'], work);
run('npm', ['run', 'build:prod'], work);

const dist = join(work, 'dist');
if (!existsSync(dist)) throw new Error(`OpenSA build finished without dist at ${dist}`);

// ---------------------------------------------------------------------------
// Legacy WebGL2 build: exact pre-deletion Three/WebGL renderer, isolated under /legacy/.
// ---------------------------------------------------------------------------
cloneAt(legacyCommit, legacyWork);

// Force the historical Three/WebGL host. That commit already kept this renderer behind ?engine=three;
// making the legacy bundle unconditional prevents another WebGPU probe after the modern shell redirected.
const legacyAppFile = join(legacyWork, 'apps', 'web', 'src', 'ui', 'shell', 'app.tsx');
let legacyApp = readFileSync(legacyAppFile, 'utf8');
legacyApp = replaceOnce(
  legacyApp,
  `const THREE_OVERRIDE = new URLSearchParams(window.location.search).get('engine') === 'three';`,
  `const THREE_OVERRIDE = true; // SPECTER /legacy is intentionally the WebGL renderer.`,
  'force historical Three/WebGL renderer',
);
writeFileSync(legacyAppFile, legacyApp);

// Build asset URLs relative to /legacy/ so the two Vite bundles can coexist under one Render hostname.
const legacyViteFile = join(legacyWork, 'vite.config.ts');
let legacyVite = readFileSync(legacyViteFile, 'utf8');
legacyVite = replaceOnce(
  legacyVite,
  'export default defineConfig(({ command }) => ({\n  build: {',
  `export default defineConfig(({ command }) => ({\n  base: '/legacy/',\n  build: {`,
  'legacy Vite base path',
);
writeFileSync(legacyViteFile, legacyVite);

run('npm', ['ci'], legacyWork);
run('npm', ['run', 'build:prod'], legacyWork);

const legacyDist = join(legacyWork, 'dist');
if (!existsSync(legacyDist)) throw new Error(`Legacy OpenSA build finished without dist at ${legacyDist}`);
const legacyOut = join(dist, 'legacy');
rmSync(legacyOut, { recursive: true, force: true });
cpSync(legacyDist, legacyOut, { recursive: true });

writeFileSync(
  join(dist, 'graphics-ladder.json'),
  JSON.stringify(
    {
      order: ['webgpu-core', 'webgpu-compatibility', 'webgl2-three'],
      sourceCommit,
      legacyCommit,
      renderHost,
    },
    null,
    2,
  ),
);

console.log(
  JSON.stringify({
    event: 'graphics_ladder_build_ok',
    sourceCommit,
    legacyCommit,
    renderHost,
    dist,
    legacyOut,
    order: ['webgpu-core', 'webgpu-compatibility', 'webgl2-three'],
  }),
);
