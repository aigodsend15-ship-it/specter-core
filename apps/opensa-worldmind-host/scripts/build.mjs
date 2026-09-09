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
const legacyCommit = process.env.OPENSA_LEGACY_COMMIT ?? 'cacc1f0b8332d3d96490e593f95c02e2ab23b1a7';
const renderHost = process.env.OPENSA_ALLOWED_HOST ?? 'specter-gtasa-rp.onrender.com';
const cloudManifest = process.env.VITE_SPECTER_GAME_MANIFEST ?? '';

function run(command, args, cwd = hostRoot) {
  const result = spawnSync(command, args, { cwd, env: process.env, stdio: 'inherit', shell: process.platform === 'win32' });
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

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function exposeSanAndreas(root, label) {
  const configFile = join(root, 'apps', 'web', 'src', 'game-config.tsx');
  let config = readFileSync(configFile, 'utf8');
  config = replaceOnce(
    config,
    `  original: {\n    assetLoader: 'local',\n    devOnly: true,`,
    `  original: {\n    assetLoader: 'local',`,
    `${label} expose GTA SA`,
  );
  config = replaceOnce(
    config,
    `label: 'Run San Andreas [local only]'`,
    `label: 'Run GTA San Andreas [select game folder]'`,
    `${label} GTA SA label`,
  );
  writeFileSync(configFile, config);
}

function wireCloud(root, label) {
  const configFile = join(root, 'apps', 'web', 'src', 'game-config.tsx');
  let config = readFileSync(configFile, 'utf8');
  config = replaceOnce(
    config,
    `  original: {\n    assetLoader: 'local',`,
    `  original: {\n    assetLoader: import.meta.env.VITE_SPECTER_GAME_MANIFEST ? 'fetch' : 'local',`,
    `${label} cloud loader`,
  );
  config = replaceOnce(
    config,
    `label: 'Run GTA San Andreas [select game folder]'`,
    `label: import.meta.env.VITE_SPECTER_GAME_MANIFEST ? 'Run SPECTER GTA RP [cloud]' : 'Run GTA San Andreas [select game folder]'`,
    `${label} cloud label`,
  );
  writeFileSync(configFile, config);

  const bootFile = join(root, 'apps', 'web', 'src', 'ui', 'shell', 'use-asset-boot.ts');
  let boot = readFileSync(bootFile, 'utf8');
  boot = replaceOnce(
    boot,
    'manifestUrl: `${BASE}/games/${state.game}-${__APP_VERSION__}/manifest.json`,',
    'manifestUrl: import.meta.env.VITE_SPECTER_GAME_MANIFEST || `${BASE}/games/${state.game}-${__APP_VERSION__}/manifest.json`,',
    `${label} manifest override`,
  );
  writeFileSync(bootFile, boot);

  const appFile = join(root, 'apps', 'web', 'src', 'ui', 'shell', 'app.tsx');
  let app = readFileSync(appFile, 'utf8');
  app = replaceOnce(
    app,
    `  useEffect(() => {\n    initAnalytics();\n  }, []);`,
    `  useEffect(() => {\n    initAnalytics();\n  }, []);\n\n  useEffect(() => {\n    if (!import.meta.env.VITE_SPECTER_GAME_MANIFEST) return;\n    if (boot.state.phase === 'menu') boot.play('original');\n    else if (boot.state.phase === 'disclaimer') boot.acceptDisclaimer();\n  }, [boot.state.phase, boot.play, boot.acceptDisclaimer]);`,
    `${label} cloud autostart`,
  );
  writeFileSync(appFile, app);
  return { appSha256: sha256(app), bootSha256: sha256(boot), configSha256: sha256(config) };
}

function allowRenderHost(root, label, base = null) {
  const file = join(root, 'vite.config.ts');
  let vite = readFileSync(file, 'utf8');
  const replacement = base
    ? `export default defineConfig(({ command }) => ({\n  base: ${JSON.stringify(base)},\n  server: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  preview: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  build: {`
    : `export default defineConfig(({ command }) => ({\n  server: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  preview: { allowedHosts: [${JSON.stringify(renderHost)}] },\n  build: {`;
  vite = replaceOnce(vite, 'export default defineConfig(({ command }) => ({\n  build: {', replacement, `${label} vite host`);
  writeFileSync(file, vite);
  return sha256(vite);
}

function wireLegacyWorldMind(root) {
  const source = join(hostRoot, 'patches', 'legacy-worldmind.ts');
  const target = join(root, 'apps', 'web', 'src', 'ui', 'legacy-worldmind.ts');
  cpSync(source, target);

  const adapterFile = join(root, 'packages', 'game', 'src', 'adapters', 'gta-sa-world.adapter.ts');
  let adapter = readFileSync(adapterFile, 'utf8');
  const adapterAnchor = `  /** Every carcol paint combo for a model (palette-index tuples) — 2-colour entries then 4-colour;`;
  const adapterMethods = `  /** SPECTER WorldMind: resolve a small prop from the actual loaded GTA catalogue. */\n  searchWorldMindModel(keyword: string): string | null {\n    const query = keyword.trim().toLowerCase();\n    if (!query || !this.defByName) return null;\n    for (const [name] of this.defByName) {\n      if (name.includes(query)) return name;\n    }\n    return null;\n  }\n\n  /** SPECTER WorldMind: build one native GTA-space prop for the shared WebGL2 overlay. */\n  async loadWorldMindProp(modelName: string): Promise<Object3D | null> {\n    await Promise.resolve();\n    const def = this.defByName?.get(modelName.toLowerCase());\n    if (!def) return null;\n    const dff = this.fs.get(\`${'${def.modelName.toLowerCase()}'} .dff\`.replace(' ', ''));\n    const txd = this.fs.get(\`${'${def.txdName.toLowerCase()}'} .txd\`.replace(' ', ''));\n    if (!dff || !txd) return null;\n    return buildClump(parseDff(dff), buildTextureMap(parseTxd(txd)), { convertToYUp: false });\n  }\n\n`;
  adapter = replaceOnce(adapter, adapterAnchor, `${adapterMethods}${adapterAnchor}`, 'legacy WorldMind adapter methods');
  writeFileSync(adapterFile, adapter);

  const canvasFile = join(root, 'apps', 'web', 'src', 'ui', 'canvas-host.tsx');
  let canvas = readFileSync(canvasFile, 'utf8');
  canvas = replaceOnce(
    canvas,
    `import { loadCityBoxes, loadGxt, loadInfoZones } from './zone-data';`,
    `import { loadCityBoxes, loadGxt, loadInfoZones } from './zone-data';\nimport { setupLegacyWorldMind } from './legacy-worldmind';`,
    'legacy WorldMind import',
  );
  canvas = replaceOnce(
    canvas,
    `    game.frameEntity(player, 12);`,
    `    game.frameEntity(player, 12);\n    // Shared persistent agent constructions are projected into the same GTA streaming root.\n    setupLegacyWorldMind(game, adapter);`,
    'legacy WorldMind bootstrap',
  );
  writeFileSync(canvasFile, canvas);

  return {
    adapterSha256: sha256(adapter),
    bridgeSha256: sha256(readFileSync(source)),
    canvasSha256: sha256(canvas),
  };
}

// Modern WebGPU runtime.
cloneAt(sourceCommit, work);
exposeSanAndreas(work, 'modern');
const modernCloud = wireCloud(work, 'modern');

const bridgeSource = join(hostRoot, 'patches', 'worldmind-agent-build.ts');
const bridgeTarget = join(work, 'apps', 'web', 'src', 'ui', 'worldmind-agent-build.ts');
mkdirSync(dirname(bridgeTarget), { recursive: true });
cpSync(bridgeSource, bridgeTarget);
const bridge = readFileSync(bridgeTarget, 'utf8');

const hostFile = join(work, 'apps', 'web', 'src', 'ui', 'engine-canvas-host.tsx');
let host = readFileSync(hostFile, 'utf8');
host = replaceOnce(host, "import { setupEngineProps } from './engine-props';", "import { setupEngineProps } from './engine-props';\nimport { setupWorldMindAgents } from './worldmind-agent-build';", 'WorldMind import');
host = replaceOnce(host, '  const props = setupEngineProps(engine, fs, physics);', "  const props = setupEngineProps(engine, fs, physics);\n  const worldMind = setupWorldMindAgents({ adapter, engine, fs, playerPosition: viewOf });", 'WorldMind setup');
host = replaceOnce(host, '      props.update();\n      propsMs = performance.now() - propsStarted;', '      props.update();\n      worldMind.update();\n      propsMs = performance.now() - propsStarted;', 'WorldMind frame');
writeFileSync(hostFile, host);

const gateSource = join(hostRoot, 'patches', 'webgpu-gate.ts');
const gateTarget = join(work, 'apps', 'web', 'src', 'ui', 'shell', 'webgpu-gate.ts');
cpSync(gateSource, gateTarget);

const appFile = join(work, 'apps', 'web', 'src', 'ui', 'shell', 'app.tsx');
let app = readFileSync(appFile, 'utf8');
app = replaceOnce(
  app,
  `    void probeWebGpu().then((probe) => {\n      if (!cancelled) {\n        setWebGpu(probe);\n      }\n    });`,
  `    void probeWebGpu().then((probe) => {\n      if (cancelled) return;\n      if (probe === 'webgl2') {\n        const target = new URL('/legacy/', window.location.origin);\n        const params = new URLSearchParams(window.location.search);\n        params.set('engine', 'three');\n        params.set('fallback', 'webgl2');\n        target.search = params.toString();\n        window.location.replace(target.toString());\n        return;\n      }\n      setWebGpu(probe);\n    });`,
  'WebGL2 redirect',
);
app = replaceOnce(app, `  if (webGpu !== null && webGpu !== 'ok') {`, `  if (webGpu !== null && webGpu !== 'ok' && webGpu !== 'compatibility' && webGpu !== 'webgl2') {`, 'graphics states');
writeFileSync(appFile, app);

const deviceFile = join(work, 'packages', 'engine', 'src', 'core', 'device.ts');
let device = readFileSync(deviceFile, 'utf8');
device = replaceOnce(
  device,
  `  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });\n  if (!adapter) {\n    throw new Error('WebGPU adapter request failed (blocklisted GPU or disabled flag?)');\n  }`,
  `  type AdapterOptionsWithLevel = GPURequestAdapterOptions & { featureLevel?: 'compatibility' | 'core' };\n  const ask = async (featureLevel: 'compatibility' | 'core', powerPreference?: GPUPowerPreference): Promise<GPUAdapter | null> => {\n    try { return await navigator.gpu.requestAdapter({ featureLevel, ...(powerPreference ? { powerPreference } : {}) } as AdapterOptionsWithLevel); } catch { return null; }\n  };\n  const adapter = (await ask('core', 'high-performance')) ?? (await ask('compatibility'));\n  if (!adapter) throw new Error('WebGPU adapter request failed in core and compatibility modes');`,
  'device ladder',
);
writeFileSync(deviceFile, device);
const viteSha = allowRenderHost(work, 'modern');

console.log(JSON.stringify({ event:'gtasa_worldmind_patch', sourceCommit, cloudManifestConfigured:Boolean(cloudManifest), modernCloud, bridgeSha256:sha256(bridge), hostSha256:sha256(host), deviceSha256:sha256(device), viteSha256:viteSha }));
run('npm', ['ci'], work);
run('npm', ['run', 'build:prod'], work);
const dist = join(work, 'dist');
if (!existsSync(dist)) throw new Error('modern dist missing');

// Historical Three/WebGL2 renderer for old Intel hardware.
cloneAt(legacyCommit, legacyWork);
exposeSanAndreas(legacyWork, 'legacy');
const legacyCloud = wireCloud(legacyWork, 'legacy');
const legacyWorldMind = wireLegacyWorldMind(legacyWork);
const legacyAppFile = join(legacyWork, 'apps', 'web', 'src', 'ui', 'shell', 'app.tsx');
let legacyApp = readFileSync(legacyAppFile, 'utf8');
legacyApp = replaceOnce(legacyApp, `const THREE_OVERRIDE = new URLSearchParams(window.location.search).get('engine') === 'three';`, `const THREE_OVERRIDE = true;`, 'force WebGL2');
writeFileSync(legacyAppFile, legacyApp);
allowRenderHost(legacyWork, 'legacy', '/legacy/');
run('npm', ['ci'], legacyWork);
run('npm', ['run', 'build:prod'], legacyWork);
const legacyDist = join(legacyWork, 'dist');
if (!existsSync(legacyDist)) throw new Error('legacy dist missing');
const legacyOut = join(dist, 'legacy');
rmSync(legacyOut, { recursive: true, force: true });
cpSync(legacyDist, legacyOut, { recursive: true });

writeFileSync(join(dist, 'graphics-ladder.json'), JSON.stringify({
  order:['webgpu-core','webgpu-compatibility','webgl2-three'], sourceCommit, legacyCommit, renderHost,
  cloudManifestConfigured:Boolean(cloudManifest), modernCloud, legacyCloud, legacyWorldMind,
  sharedWorldMind:Boolean(process.env.VITE_SUPABASE_URL),
}, null, 2));
console.log(JSON.stringify({ event:'gtasa_worldmind_build_ok', order:['webgpu-core','webgpu-compatibility','webgl2-three'], cloudManifestConfigured:Boolean(cloudManifest), sharedWorldMind:Boolean(process.env.VITE_SUPABASE_URL), legacyWorldMind:true, dist, legacyOut }));
