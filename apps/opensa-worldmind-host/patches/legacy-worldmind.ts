import type { Game } from '@opensa/game';
import type { GtaSaWorldAdapter } from '@opensa/game/adapters/gta-sa-world.adapter';
import type { Object3D } from 'three';

const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? '';
const SUPABASE_KEY = (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined) ?? '';
const REFRESH_MS = 10_000;

interface SharedRow {
  stable_key: string;
  owner_agent_id: string | null;
  asset_key: string;
  transform: unknown;
}

interface WorldMindLegacyAdapter extends GtaSaWorldAdapter {
  searchWorldMindModel(keyword: string): string | null;
  loadWorldMindProp(modelName: string): Promise<Object3D | null>;
}

interface Transform {
  position: [number, number, number];
  heading: number;
}

/**
 * Renderer-independent WorldMind projection for the historical Three/WebGL2 host.
 * Shared state remains authoritative in Supabase; the browser is read-only.
 */
export function setupLegacyWorldMind(game: Game, baseAdapter: GtaSaWorldAdapter): () => void {
  if (!SUPABASE_URL || !SUPABASE_KEY) return () => undefined;

  const adapter = baseAdapter as WorldMindLegacyAdapter;
  const root = game.getStreamingRoot();
  const live = new Map<string, { asset: string; heading: number; object: Object3D; position: [number, number, number] }>();
  let stopped = false;
  let syncing = false;

  const remove = (id: string): void => {
    const entry = live.get(id);
    if (!entry) return;
    root.remove(entry.object);
    disposeObject(entry.object);
    live.delete(id);
  };

  const resolve = (assetKey: string): string | null => {
    if (!assetKey.startsWith('search:')) return assetKey;
    return adapter.searchWorldMindModel(assetKey.slice('search:'.length));
  };

  const sync = async (): Promise<void> => {
    if (stopped || syncing) return;
    syncing = true;
    try {
      const url = `${SUPABASE_URL}/rest/v1/world_objects?select=stable_key,owner_agent_id,asset_key,transform&active=eq.true&order=created_at.asc`;
      const response = await fetch(url, {
        cache: 'no-store',
        headers: { apikey: SUPABASE_KEY, authorization: `Bearer ${SUPABASE_KEY}` },
      });
      if (!response.ok) throw new Error(`worldmind-overlay-${response.status}`);
      const rows = (await response.json()) as SharedRow[];
      const desired = new Set<string>();

      for (const row of rows) {
        if (typeof row.stable_key !== 'string' || typeof row.asset_key !== 'string') continue;
        const transform = parseTransform(row.transform);
        if (!transform) continue;
        const id = `worldmind:${row.stable_key}`;
        desired.add(id);
        const modelName = resolve(row.asset_key);
        if (!modelName) continue;

        const previous = live.get(id);
        if (
          previous &&
          previous.asset === modelName &&
          previous.heading === transform.heading &&
          samePosition(previous.position, transform.position)
        ) continue;

        if (previous) remove(id);
        const object = await adapter.loadWorldMindProp(modelName);
        if (!object || stopped) continue;
        // The streaming root already performs GTA Z-up -> Three Y-up. Keep this object in native GTA space.
        object.position.set(transform.position[0], transform.position[1], transform.position[2]);
        object.rotation.z = transform.heading;
        object.userData.worldMind = { owner: row.owner_agent_id ?? 'worldmind', stableKey: row.stable_key };
        root.add(object);
        live.set(id, { asset: modelName, heading: transform.heading, object, position: transform.position });
      }

      for (const id of [...live.keys()]) if (!desired.has(id)) remove(id);
    } catch (error) {
      // One compact diagnostic; failure of the control plane must never stop GTA gameplay.
      console.warn('[WorldMind/WebGL2] shared overlay sync failed', error instanceof Error ? error.message : String(error));
    } finally {
      syncing = false;
    }
  };

  void sync();
  const timer = window.setInterval(() => void sync(), REFRESH_MS);
  return (): void => {
    stopped = true;
    window.clearInterval(timer);
    for (const id of [...live.keys()]) remove(id);
  };
}

function parseTransform(value: unknown): Transform | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as { position?: unknown; heading?: unknown };
  if (!Array.isArray(row.position) || row.position.length !== 3 || !row.position.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
  return {
    position: [row.position[0], row.position[1], row.position[2]],
    heading: typeof row.heading === 'number' && Number.isFinite(row.heading) ? row.heading : 0,
  };
}

function samePosition(a: readonly number[], b: readonly number[]): boolean {
  return Math.abs((a[0] ?? 0) - (b[0] ?? 0)) < 0.001 && Math.abs((a[1] ?? 0) - (b[1] ?? 0)) < 0.001 && Math.abs((a[2] ?? 0) - (b[2] ?? 0)) < 0.001;
}

function disposeObject(object: Object3D): void {
  object.traverse((node) => {
    const mesh = node as Object3D & { geometry?: { dispose?: () => void }; material?: unknown };
    mesh.geometry?.dispose?.();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    for (const material of materials) (material as { dispose?: () => void }).dispose?.();
  });
}
