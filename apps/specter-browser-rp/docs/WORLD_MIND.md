# WorldMind v0.3

West Coast now has a persistent autonomous simulation layer.

## Runtime layers

- Three.js renders the browser world.
- cannon-es provides rigid-body physics and raycast vehicle suspension.
- The Node WebSocket server validates player state and broadcasts snapshots.
- SQLite WAL stores player state, NPC memory/goals/resources, world time and constructions.
- NPCs run a deterministic zero-cost planner continuously. Builders gather materials and create persistent world objects.
- NPC dialogue can optionally call an OpenAI-compatible model endpoint server-side. If the endpoint is unavailable, dialogue falls back to the deterministic in-world agent.

## Optional NPC LLM

Environment variables:

- `NPC_AI_BASE_URL` — OpenAI-compatible `/v1` base URL.
- `NPC_AI_MODEL` — model identifier.
- `NPC_AI_API_KEY` — optional; omitted for keyless endpoints.

The server never exposes provider credentials to the browser. NPC memory included in a dialogue request is bounded to the latest in-world events.

## Controls

WASD move/drive, mouse camera, Shift sprint, Space jump/handbrake, E enter/exit car, F talk to nearby NPC, B build, R reset car, Enter chat.

## GTA-SA compatibility

`public/gtasa-loader.js` is intentionally retained as the private-authorized asset compatibility bridge. No Rockstar assets are part of the public deployment.
