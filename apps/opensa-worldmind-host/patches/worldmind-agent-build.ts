/**
 * SPECTER WorldMind construction bridge for OpenSA.
 *
 * Capability-gated autonomous agents may place persistent, renderable objects from the
 * user's locally selected GTA install. The bridge never reads outside OpenSA's VFS and
 * never grants shell/filesystem/network credentials to an agent.
 *
 * Persistence is browser-local because OpenSA's GTA files are browser-local too. The
 * mutation ledger can later be federated to a server without changing the capability API.
 */
import type { Engine, VehicleInstance, VehicleModelId } from '@opensa/engine';
import type { GtaSaWorldAdapter } from '@opensa/game/adapters/gta-sa-world.adapter';
import type { AssetFileSystem } from '@opensa/renderware';

import { gtaPositionToEngine, writeGtaRoot } from '@opensa/game/adapters/engine-vehicle-handle';
import { readModelOsm } from '@opensa/game/adapters/vehicle-osm';
import { toRigidModelInit } from '@opensa/game/adapters/vehicle-model-init';
import { getClump, getTxdChain } from '@opensa/renderware/archive/asset-cache';
import { buildVehicleModel } from '@opensa/renderware/vehicle/build-vehicle-model';
import { VehicleTextures } from '@opensa/renderware/vehicle/textures';

const STORAGE_KEY = 'specter.worldmind.gtasa.builds.v1';
const SETTINGS_KEY = 'specter.worldmind.gtasa.settings.v1';
const MAX_BUILDS = 64;
const AUTO_BUILD_MS = 30_000;
const RESTORE_INTERVAL_MS = 300;
const HUD_ID = 'specter-worldmind-hud';

export type WorldCapability =
  | 'world.inspect'
  | 'world.build'
  | 'world.modify_owned'
  | 'world.demolish_owned'
  | 'world.harvest'
  | 'world.trade'
  | 'world.create_job';

const CONSTRUCTIVE_CAPABILITIES: ReadonlySet<WorldCapability> = new Set([
  'world.inspect',
  'world.build',
  'world.modify_owned',
  'world.demolish_owned',
  'world.harvest',
  'world.trade',
  'world.create_job',
]);

interface WorldMindAgent {
  id: string;
  name: string;
  role: 'builder' | 'planner' | 'scout';
  capabilities: ReadonlySet<WorldCapability>;
}

interface BuildRecord {
  agentId: string;
  createdAt: number;
  heading: number;
  id: string;
  modelName: string;
  position: [number, number, number];
  revision: number;
}

interface BridgeDeps {
  adapter: GtaSaWorldAdapter;
  engine: Engine;
  fs: AssetFileSystem;
  playerPosition: () => [number, number, number];
}

export interface WorldMindAgents {
  update(): void;
}

const AGENTS: readonly WorldMindAgent[] = [
  agent('wm-forge', 'Forge', 'builder'),
  agent('wm-aether', 'Aether', 'planner'),
  agent('wm-nyx', 'Nyx', 'scout'),
  agent('wm-helios', 'Helios', 'planner'),
  agent('wm-mason', 'Mason', 'builder'),
  agent('wm-aria', 'Aria', 'builder'),
];

function agent(id: string, name: string, role: WorldMindAgent['role']): WorldMindAgent {
  return { capabilities: CONSTRUCTIVE_CAPABILITIES, id, name, role };
}

/**
 * Installs the autonomous construction governor.
 *
 * Hard safety invariant: the only executable authority an agent receives is the explicit
 * world capability set above. There is no process, VFS-write, credential, Git, or arbitrary
 * fetch primitive in this surface.
 */
export function setupWorldMindAgents({ adapter, engine, fs, playerPosition }: BridgeDeps): WorldMindAgents {
  const models = new Map<string, null | VehicleModelId>();
  const live = new Map<string, VehicleInstance>();
  const records = loadRecords();
  let enabled = loadEnabled();
  let candidates: string[] | null = null;
  let restoreCursor = 0;
  let nextRestoreAt = performance.now();
  let nextAutoAt = performance.now() + 4_000;
  let lastHudCount = -1;
  let lastHudEnabled = !enabled;
  let warned = false;

  const root = new Float32Array(16);

  const modelOf = (modelName: string): null | VehicleModelId => {
    const key = modelName.toLowerCase();
    const cached = models.get(key);
    if (cached !== undefined) return cached;

    let id: null | VehicleModelId = null;
    try {
      const osm = fs.get(`${key}.osm`);
      if (osm) {
        id = engine.createVehicleModel(readModelOsm(modelName, new Uint8Array(osm)).model);
      } else {
        const txd = adapter.txdOf(modelName);
        if (!txd) {
          models.set(key, null);
          return null;
        }
        id = engine.createVehicleModel(
          toRigidModelInit(buildVehicleModel(getClump(fs, modelName), new VehicleTextures(getTxdChain(fs, txd)))),
        );
      }
    } catch (error) {
      // A single modded/non-rigid model must never take down the game loop.
      if (!warned) {
        warned = true;
        // eslint-disable-next-line no-console -- one diagnostic, not per-frame narration
        console.warn('[WorldMind] one construction model could not be instantiated', error);
      }
      id = null;
    }
    models.set(key, id);
    return id;
  };

  const materialize = (record: BuildRecord): boolean => {
    if (live.has(record.id)) return true;
    const model = modelOf(record.modelName);
    if (model === null) return false;
    const instance = engine.createVehicle(model);
    const half = record.heading * 0.5;
    const q: [number, number, number, number] = [0, 0, Math.sin(half), Math.cos(half)];
    writeGtaRoot(root, gtaPositionToEngine(record.position), q);
    instance.entity.setRoot(root);
    live.set(record.id, instance);
    engine.updateVehicles();
    return true;
  };

  const catalogue = (): string[] => {
    if (candidates !== null) return candidates;
    const index = adapter.modelIndex();
    if (!index) return [];
    // Discover from the actual selected installation rather than assuming a stock model list.
    const keywords = ['fence', 'bench', 'chair', 'table', 'plant', 'lamp', 'barrel', 'crate', 'tree'];
    const seen = new Set<string>();
    const found: string[] = [];
    for (const keyword of keywords) {
      for (const hit of index.search(keyword, 8)) {
        const key = hit.name.toLowerCase();
        if (!seen.has(key) && adapter.txdOf(hit.name)) {
          seen.add(key);
          found.push(hit.name);
        }
      }
    }
    if (found.length > 0) candidates = found;
    return found;
  };

  const build = (agentIndex: number): BuildRecord | null => {
    const actor = AGENTS[agentIndex % AGENTS.length];
    if (!enabled || !actor.capabilities.has('world.build') || records.length >= MAX_BUILDS) return null;
    const choices = catalogue();
    if (choices.length === 0) return null;

    const [px, py, pz] = playerPosition();
    const serial = records.length;
    // Golden-angle ring: deterministic, spatially spread, always near the active player.
    const angle = (serial * 2.399963229728653 + agentIndex * 0.71) % (Math.PI * 2);
    const radius = 7 + (serial % 5) * 2.25;
    const position: [number, number, number] = [
      px + Math.cos(angle) * radius,
      py + Math.sin(angle) * radius,
      // OpenSA's player capsule is 0.90 m from centre to foot. Most map props author their origin at ground.
      pz - 0.9,
    ];

    // Keep the builder from stacking every object on a previous mutation.
    if (records.some((item) => distance2(item.position, position) < 3.2 * 3.2)) return null;

    const now = Date.now();
    const record: BuildRecord = {
      agentId: actor.id,
      createdAt: now,
      heading: angle + Math.PI * 0.5,
      id: `${actor.id}:${now}:${serial}`,
      modelName: choices[(serial * 7 + agentIndex * 3) % choices.length],
      position,
      revision: 1,
    };

    if (!materialize(record)) return null;
    records.push(record);
    saveRecords(records);
    return record;
  };

  const demolishLastOwned = (): boolean => {
    const record = records.at(-1);
    if (!record) return false;
    const actor = AGENTS.find((item) => item.id === record.agentId);
    if (!actor?.capabilities.has('world.demolish_owned')) return false;
    const instance = live.get(record.id);
    if (instance) {
      engine.destroyVehicle(instance);
      live.delete(record.id);
      engine.updateVehicles();
    }
    records.pop();
    saveRecords(records);
    return true;
  };

  const setEnabled = (value: boolean): void => {
    enabled = value;
    saveEnabled(value);
    updateHud(true);
  };

  const updateHud = (force = false): void => {
    if (!force && records.length === lastHudCount && enabled === lastHudEnabled) return;
    lastHudCount = records.length;
    lastHudEnabled = enabled;
    const hud = ensureHud();
    const status = hud.querySelector<HTMLElement>('[data-worldmind-status]');
    if (status) status.textContent = `${AGENTS.length} agents · BUILD ${enabled ? 'ON' : 'OFF'} · ${records.length}/${MAX_BUILDS}`;
    const toggle = hud.querySelector<HTMLButtonElement>('[data-worldmind-toggle]');
    if (toggle) toggle.textContent = enabled ? 'Pause agents [F7]' : 'Enable agents [F7]';
  };

  const api = {
    agents: AGENTS.map((item) => ({ id: item.id, name: item.name, role: item.role, capabilities: [...item.capabilities] })),
    buildNow: (): BuildRecord | null => build(records.length % AGENTS.length),
    capabilities: [...CONSTRUCTIVE_CAPABILITIES],
    demolishLast: demolishLastOwned,
    listBuilds: (): readonly BuildRecord[] => records.map((item) => ({ ...item, position: [...item.position] })),
    setEnabled,
    version: 'worldmind-opensa-1',
  };
  (window as Window & { __SPECTER_WORLDMIND__?: unknown }).__SPECTER_WORLDMIND__ = api;

  const onKey = (event: KeyboardEvent): void => {
    if (event.repeat) return;
    if (event.code === 'F7') {
      event.preventDefault();
      setEnabled(!enabled);
    } else if (event.code === 'F8') {
      event.preventDefault();
      build(records.length % AGENTS.length);
      updateHud(true);
    } else if (event.code === 'F9') {
      event.preventDefault();
      demolishLastOwned();
      updateHud(true);
    }
  };
  window.addEventListener('keydown', onKey);

  updateHud(true);

  return {
    update(): void {
      const now = performance.now();

      // Rehydrate at most one persisted object per slice; a 64-object save must not become one giant boot frame.
      if (restoreCursor < records.length && now >= nextRestoreAt) {
        materialize(records[restoreCursor]);
        restoreCursor += 1;
        nextRestoreAt = now + RESTORE_INTERVAL_MS;
      }

      if (enabled && restoreCursor >= records.length && records.length < MAX_BUILDS && now >= nextAutoAt) {
        build(records.length % AGENTS.length);
        nextAutoAt = now + AUTO_BUILD_MS;
      }
      updateHud();
    },
  };
}

function distance2(a: readonly number[], b: readonly number[]): number {
  const dx = (a[0] ?? 0) - (b[0] ?? 0);
  const dy = (a[1] ?? 0) - (b[1] ?? 0);
  return dx * dx + dy * dy;
}

function loadRecords(): BuildRecord[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isBuildRecord).slice(0, MAX_BUILDS);
  } catch {
    return [];
  }
}

function isBuildRecord(value: unknown): value is BuildRecord {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<BuildRecord>;
  return (
    typeof item.id === 'string' &&
    typeof item.agentId === 'string' &&
    typeof item.modelName === 'string' &&
    typeof item.heading === 'number' &&
    Array.isArray(item.position) &&
    item.position.length === 3 &&
    item.position.every((n) => typeof n === 'number' && Number.isFinite(n))
  );
}

function saveRecords(records: readonly BuildRecord[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  } catch {
    // Local storage can be unavailable in hardened/private browser contexts; gameplay remains live.
  }
}

function loadEnabled(): boolean {
  const query = new URLSearchParams(location.search);
  if (query.get('agents') === '0') return false;
  try {
    const stored = localStorage.getItem(SETTINGS_KEY);
    return stored === null ? true : JSON.parse(stored).enabled !== false;
  } catch {
    return true;
  }
}

function saveEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enabled }));
  } catch {
    // Best-effort preference only.
  }
}

function ensureHud(): HTMLElement {
  const existing = document.getElementById(HUD_ID);
  if (existing) return existing;

  const hud = document.createElement('section');
  hud.id = HUD_ID;
  hud.setAttribute('aria-label', 'SPECTER WorldMind');
  Object.assign(hud.style, {
    position: 'fixed',
    left: '14px',
    bottom: '14px',
    zIndex: '2147483000',
    minWidth: '235px',
    padding: '10px 12px',
    border: '1px solid rgba(120,255,210,.45)',
    borderRadius: '10px',
    background: 'rgba(5,10,14,.78)',
    color: '#d9fff2',
    font: '12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    backdropFilter: 'blur(7px)',
    pointerEvents: 'auto',
  });
  hud.innerHTML = `
    <strong style="display:block;letter-spacing:.08em">SPECTER WORLDMIND</strong>
    <span data-worldmind-status style="display:block;margin:4px 0 8px"></span>
    <button data-worldmind-toggle type="button" style="font:inherit;padding:4px 7px;cursor:pointer">Pause agents [F7]</button>
    <span style="display:block;margin-top:6px;opacity:.72">F8 build now · F9 undo last</span>
  `;
  hud.querySelector<HTMLButtonElement>('[data-worldmind-toggle]')?.addEventListener('click', () => {
    const api = (window as Window & { __SPECTER_WORLDMIND__?: { setEnabled?: (enabled: boolean) => void } })
      .__SPECTER_WORLDMIND__;
    const status = hud.querySelector<HTMLElement>('[data-worldmind-status]')?.textContent ?? '';
    api?.setEnabled?.(!status.includes('BUILD ON'));
  });
  document.body.appendChild(hud);
  return hud;
}
