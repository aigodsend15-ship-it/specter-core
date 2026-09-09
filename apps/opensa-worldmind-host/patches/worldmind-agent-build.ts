/**
 * SPECTER WorldMind bridge for OpenSA.
 *
 * Two layers coexist:
 *  - local laboratory builds, stored in localStorage;
 *  - promoted shared builds, read-only in the browser from Supabase/Postgres.
 *
 * Shared agents never receive browser, shell, Git or credential authority. They propose mutations to the
 * trusted server control-plane; only promoted `world_objects` are rendered here.
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

const STORAGE_KEY = 'specter.worldmind.gtasa.builds.v2';
const SETTINGS_KEY = 'specter.worldmind.gtasa.settings.v2';
const MAX_LOCAL_BUILDS = 48;
const AUTO_BUILD_MS = 45_000;
const RESTORE_INTERVAL_MS = 250;
const CLOUD_REFRESH_MS = 10_000;
const HUD_ID = 'specter-worldmind-hud';

const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? '';
const SUPABASE_KEY = (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined) ?? '';

export type WorldCapability =
  | 'world.inspect'
  | 'world.build'
  | 'world.modify_owned'
  | 'world.demolish_owned'
  | 'world.harvest'
  | 'world.trade'
  | 'world.create_job';

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
  cloud?: boolean;
}

interface CloudObject {
  stable_key: string;
  owner_agent_id: string | null;
  asset_key: string;
  transform: unknown;
}

interface CloudTransform {
  position: [number, number, number];
  heading: number;
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

const ALL_CAPS: ReadonlySet<WorldCapability> = new Set([
  'world.inspect', 'world.build', 'world.modify_owned', 'world.demolish_owned',
  'world.harvest', 'world.trade', 'world.create_job',
]);

const AGENTS: readonly WorldMindAgent[] = [
  { id: 'forge', name: 'Forge', role: 'builder', capabilities: ALL_CAPS },
  { id: 'aether', name: 'Aether', role: 'planner', capabilities: ALL_CAPS },
  { id: 'nyx', name: 'Nyx', role: 'scout', capabilities: ALL_CAPS },
  { id: 'helios', name: 'Helios', role: 'planner', capabilities: ALL_CAPS },
];

export function setupWorldMindAgents({ adapter, engine, fs, playerPosition }: BridgeDeps): WorldMindAgents {
  const modelIds = new Map<string, null | VehicleModelId>();
  const keywordModels = new Map<string, string | null>();
  const live = new Map<string, VehicleInstance>();
  const localRecords = loadRecords();
  const cloudRecords = new Map<string, BuildRecord>();
  const root = new Float32Array(16);

  let enabled = loadEnabled();
  let candidates: string[] | null = null;
  let restoreCursor = 0;
  let nextRestoreAt = performance.now();
  let nextAutoAt = performance.now() + 8_000;
  let nextCloudAt = performance.now() + 1_000;
  let cloudLoading = false;
  let cloudError = '';
  let cloudRevisionObjects = 0;
  let lastHud = '';
  let warnedModel = false;

  const exactModel = (modelName: string): null | VehicleModelId => {
    const key = modelName.toLowerCase();
    const cached = modelIds.get(key);
    if (cached !== undefined) return cached;

    let id: null | VehicleModelId = null;
    try {
      const osm = fs.get(`${key}.osm`);
      if (osm) {
        id = engine.createVehicleModel(readModelOsm(modelName, new Uint8Array(osm)).model);
      } else {
        const txd = adapter.txdOf(modelName);
        if (!txd) {
          modelIds.set(key, null);
          return null;
        }
        id = engine.createVehicleModel(
          toRigidModelInit(buildVehicleModel(getClump(fs, modelName), new VehicleTextures(getTxdChain(fs, txd)))),
        );
      }
    } catch (error) {
      if (!warnedModel) {
        warnedModel = true;
        console.warn('[WorldMind] a promoted construction model could not be instantiated', error);
      }
      id = null;
    }
    modelIds.set(key, id);
    return id;
  };

  const resolveKeyword = (keyword: string): string | null => {
    const normalized = keyword.toLowerCase();
    const cached = keywordModels.get(normalized);
    if (cached !== undefined) return cached;
    const index = adapter.modelIndex();
    if (!index) return null;
    for (const hit of index.search(normalized, 16)) {
      if (adapter.txdOf(hit.name) || fs.get(`${hit.name.toLowerCase()}.osm`)) {
        keywordModels.set(normalized, hit.name);
        return hit.name;
      }
    }
    keywordModels.set(normalized, null);
    return null;
  };

  const resolveAsset = (asset: string): string | null =>
    asset.startsWith('search:') ? resolveKeyword(asset.slice('search:'.length)) : asset;

  const materialize = (record: BuildRecord): boolean => {
    if (live.has(record.id)) return true;
    const resolved = resolveAsset(record.modelName);
    if (!resolved) return false;
    const model = exactModel(resolved);
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

  const buildLocal = (agentIndex: number): BuildRecord | null => {
    const actor = AGENTS[agentIndex % AGENTS.length];
    if (!enabled || !actor.capabilities.has('world.build') || localRecords.length >= MAX_LOCAL_BUILDS) return null;
    const choices = catalogue();
    if (choices.length === 0) return null;
    const [px, py, pz] = playerPosition();
    const serial = localRecords.length;
    const angle = (serial * 2.399963229728653 + agentIndex * 0.71) % (Math.PI * 2);
    const radius = 7 + (serial % 5) * 2.25;
    const position: [number, number, number] = [px + Math.cos(angle) * radius, py + Math.sin(angle) * radius, pz - 0.9];
    if (localRecords.some((item) => distance2(item.position, position) < 10.24)) return null;
    const now = Date.now();
    const record: BuildRecord = {
      agentId: actor.id,
      createdAt: now,
      heading: angle + Math.PI * 0.5,
      id: `local:${actor.id}:${now}:${serial}`,
      modelName: choices[(serial * 7 + agentIndex * 3) % choices.length],
      position,
      revision: 1,
    };
    if (!materialize(record)) return null;
    localRecords.push(record);
    saveRecords(localRecords);
    return record;
  };

  const demolishLastLocal = (): boolean => {
    const record = localRecords.length > 0 ? localRecords[localRecords.length - 1] : undefined;
    if (!record) return false;
    const instance = live.get(record.id);
    if (instance) {
      engine.destroyVehicle(instance);
      live.delete(record.id);
      engine.updateVehicles();
    }
    localRecords.pop();
    saveRecords(localRecords);
    return true;
  };

  const parseTransform = (value: unknown): CloudTransform | null => {
    if (!value || typeof value !== 'object') return null;
    const item = value as { position?: unknown; heading?: unknown };
    if (!Array.isArray(item.position) || item.position.length !== 3 || !item.position.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
    const heading = typeof item.heading === 'number' && Number.isFinite(item.heading) ? item.heading : 0;
    return { position: [item.position[0], item.position[1], item.position[2]], heading };
  };

  const syncCloud = async (): Promise<void> => {
    if (!SUPABASE_URL || !SUPABASE_KEY || cloudLoading) return;
    cloudLoading = true;
    try {
      const url = `${SUPABASE_URL}/rest/v1/world_objects?select=stable_key,asset_key,transform,owner_agent_id&active=eq.true&order=created_at.asc`;
      const response = await fetch(url, { headers: { apikey: SUPABASE_KEY, authorization: `Bearer ${SUPABASE_KEY}` }, cache: 'no-store' });
      if (!response.ok) throw new Error(`cloud overlay ${response.status}`);
      const rows = (await response.json()) as CloudObject[];
      const desired = new Set<string>();
      for (const row of rows) {
        const transform = parseTransform(row.transform);
        if (!transform || typeof row.stable_key !== 'string' || typeof row.asset_key !== 'string') continue;
        const id = `cloud:${row.stable_key}`;
        desired.add(id);
        const previous = cloudRecords.get(id);
        const next: BuildRecord = {
          agentId: row.owner_agent_id ?? 'worldmind',
          createdAt: Date.now(),
          heading: transform.heading,
          id,
          modelName: row.asset_key,
          position: transform.position,
          revision: 1,
          cloud: true,
        };
        const changed = !previous || previous.modelName !== next.modelName || previous.heading !== next.heading || distance2(previous.position, next.position) > 0.0001 || previous.position[2] !== next.position[2];
        if (changed && live.has(id)) {
          engine.destroyVehicle(live.get(id)!);
          live.delete(id);
        }
        cloudRecords.set(id, next);
        materialize(next);
      }
      for (const [id] of cloudRecords) {
        if (!desired.has(id)) {
          const instance = live.get(id);
          if (instance) engine.destroyVehicle(instance);
          live.delete(id);
          cloudRecords.delete(id);
        }
      }
      engine.updateVehicles();
      cloudRevisionObjects = cloudRecords.size;
      cloudError = '';
    } catch (error) {
      cloudError = error instanceof Error ? error.message : String(error);
    } finally {
      cloudLoading = false;
    }
  };

  const setEnabled = (value: boolean): void => {
    enabled = value;
    saveEnabled(value);
    updateHud(true);
  };

  const updateHud = (force = false): void => {
    const mode = SUPABASE_URL && SUPABASE_KEY ? 'CLOUD' : 'LOCAL';
    const text = `${AGENTS.length} agents · ${mode} · BUILD ${enabled ? 'ON' : 'OFF'} · shared ${cloudRevisionObjects} · local ${localRecords.length}${cloudError ? ' · sync!' : ''}`;
    if (!force && text === lastHud) return;
    lastHud = text;
    const hud = ensureHud();
    const status = hud.querySelector<HTMLElement>('[data-worldmind-status]');
    if (status) status.textContent = text;
    const toggle = hud.querySelector<HTMLButtonElement>('[data-worldmind-toggle]');
    if (toggle) toggle.textContent = enabled ? 'Pause local lab [F7]' : 'Enable local lab [F7]';
  };

  const api = {
    agents: AGENTS.map((item) => ({ id: item.id, name: item.name, role: item.role, capabilities: [...item.capabilities] })),
    buildNow: (): BuildRecord | null => buildLocal(localRecords.length % AGENTS.length),
    demolishLast: demolishLastLocal,
    listBuilds: (): readonly BuildRecord[] => [...cloudRecords.values(), ...localRecords].map((item) => ({ ...item, position: [...item.position] as [number, number, number] })),
    refreshCloud: (): Promise<void> => syncCloud(),
    setEnabled,
    version: 'worldmind-opensa-2',
  };
  (window as Window & { __SPECTER_WORLDMIND__?: unknown }).__SPECTER_WORLDMIND__ = api;

  window.addEventListener('keydown', (event) => {
    if (event.repeat) return;
    if (event.code === 'F7') { event.preventDefault(); setEnabled(!enabled); }
    if (event.code === 'F8') { event.preventDefault(); buildLocal(localRecords.length % AGENTS.length); updateHud(true); }
    if (event.code === 'F9') { event.preventDefault(); demolishLastLocal(); updateHud(true); }
    if (event.code === 'F10') { event.preventDefault(); void syncCloud(); }
  });

  updateHud(true);

  return {
    update(): void {
      const now = performance.now();
      if (restoreCursor < localRecords.length && now >= nextRestoreAt) {
        materialize(localRecords[restoreCursor]);
        restoreCursor += 1;
        nextRestoreAt = now + RESTORE_INTERVAL_MS;
      }
      if (now >= nextCloudAt) {
        nextCloudAt = now + CLOUD_REFRESH_MS;
        void syncCloud();
      }
      if (enabled && restoreCursor >= localRecords.length && localRecords.length < MAX_LOCAL_BUILDS && now >= nextAutoAt) {
        buildLocal(localRecords.length % AGENTS.length);
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
    return parsed.filter(isBuildRecord).slice(0, MAX_LOCAL_BUILDS);
  } catch { return []; }
}

function isBuildRecord(value: unknown): value is BuildRecord {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<BuildRecord>;
  return typeof item.id === 'string' && typeof item.agentId === 'string' && typeof item.modelName === 'string' && typeof item.heading === 'number' && Array.isArray(item.position) && item.position.length === 3 && item.position.every((n) => typeof n === 'number' && Number.isFinite(n));
}

function saveRecords(records: readonly BuildRecord[]): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(records)); } catch { /* optional local lab */ }
}

function loadEnabled(): boolean {
  if (new URLSearchParams(location.search).get('agents') === '0') return false;
  try {
    const stored = localStorage.getItem(SETTINGS_KEY);
    return stored === null ? false : JSON.parse(stored).enabled === true;
  } catch { return false; }
}

function saveEnabled(enabled: boolean): void {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enabled })); } catch { /* optional preference */ }
}

function ensureHud(): HTMLElement {
  const existing = document.getElementById(HUD_ID);
  if (existing) return existing;
  const hud = document.createElement('section');
  hud.id = HUD_ID;
  hud.setAttribute('aria-label', 'SPECTER WorldMind');
  Object.assign(hud.style, {
    position: 'fixed', left: '14px', bottom: '14px', zIndex: '2147483000', minWidth: '280px', padding: '10px 12px',
    border: '1px solid rgba(120,255,210,.45)', borderRadius: '10px', background: 'rgba(5,10,14,.78)', color: '#d9fff2',
    font: '12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', backdropFilter: 'blur(7px)', pointerEvents: 'auto',
  });
  hud.innerHTML = `<strong style="display:block;letter-spacing:.08em">SPECTER WORLDMIND</strong><span data-worldmind-status style="display:block;margin:4px 0 8px"></span><button data-worldmind-toggle type="button" style="font:inherit;padding:4px 7px;cursor:pointer">Local lab [F7]</button><span style="display:block;margin-top:6px;opacity:.72">F8 local build · F9 undo local · F10 cloud sync</span>`;
  hud.querySelector<HTMLButtonElement>('[data-worldmind-toggle]')?.addEventListener('click', () => {
    const api = (window as Window & { __SPECTER_WORLDMIND__?: { setEnabled?: (enabled: boolean) => void } }).__SPECTER_WORLDMIND__;
    const status = hud.querySelector<HTMLElement>('[data-worldmind-status]')?.textContent ?? '';
    api?.setEnabled?.(!status.includes('BUILD ON'));
  });
  document.body.appendChild(hud);
  return hud;
}
