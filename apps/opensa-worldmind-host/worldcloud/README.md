# SPECTER WorldCloud v1

Purpose: make the game world cloud-native while keeping the engine reproducible and the autonomous agents auditable.

## Runtime layout

- `base/` — immutable base game/total-conversion content that the operator is authorized to host.
- `world/` — SPECTER-owned open-source overlay: DFF/TXD/COL/IPL/IDE, interiors, roads, props, vegetation, nav data, procedural builds.
- `state/` — persistent logical world state in Postgres: agents, memories, projects, object ownership, economy, revisions and provenance.
- `manifests/` — immutable content-addressed manifests promoted to `current.json` only after validation.

The browser never writes into `base/`. Agents may only propose mutations against `world/` through capability-gated operations. A promoted revision is immutable; rollback is a pointer change.

## Delivery

Preferred production storage: S3-compatible object storage (Supabase Storage or Cloudflare R2). The OpenSA fetch pipeline already supports packed, content-hashed chunks; use roughly 32–50 MiB chunks to avoid giant single requests and keep WebGL2-era machines stable.

GitHub remains the source of truth for code, schemas, small manifests, agent policies and generated metadata. Do not use the normal Git repository as the primary multi-gigabyte asset CDN.

## Graphics ladder

1. WebGPU Core
2. WebGPU Compatibility
3. WebGL2 / historical OpenSA Three renderer

All three consume the same promoted world revision.

## Agent mutation contract

Every mutation must include:

- stable `agent_id`
- declared capability
- deterministic payload
- expected parent world revision
- spatial bounds
- asset provenance/license
- SHA-256 of referenced blobs
- validation result
- rollback metadata

A mutation is promoted only after schema validation, collision/nav checks, object-budget checks and a performance gate. Failed proposals remain recorded but never become visible in `current`.

## Storage targets

Suggested buckets:

- `specter-base` — read-only runtime base
- `specter-world` — immutable revision blobs
- `specter-public` — promoted manifests/chunks exposed to the browser

Suggested public paths:

```text
specter-public/
  games/specter-current/manifest.json
  chunks/<sha256>.bin
  manifests/current.json
  manifests/revisions/<revision>.json
```

## Persistence

Apply `schema.sql` to the project database. Browser clients should have read-only access to promoted world data. Agent/server writes go through a trusted service role or validated RPC/Edge Function, never a public unrestricted table write.
