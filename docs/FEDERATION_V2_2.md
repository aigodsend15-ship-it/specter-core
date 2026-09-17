# Specter Federation V2.2

## Goal

Turn BITOS into an owner-controlled coordination HUB for local and remote reasoning workers while keeping execution authority, receipts, checkpoints and policy local.

## Verified bootstrap state

- SMP/1 typed agent language exists and is implemented in `Core/specter_mesh_protocol.py`.
- Durable federation peer/message/meeting registry exists in `Core/federation_hub_v22.py` using SQLite WAL.
- Loopback HUB API exists in `Core/hub_server.py`; the BITOS bootstrap validated `/health` locally on port 9765.
- OpenCode 1.18.30 is installed on BITOS, but model completion capacity must be proven by an E2E receipt before it is treated as AVAILABLE.
- The latest Hermes TCP probe on `127.0.0.1:51463` timed out, so Hermes is OFFLINE until new evidence proves otherwise.

## SMP/1 federation flow

`HELLO -> CAPACITY -> TASK -> ACK -> FINDING/PROPOSAL -> VERDICT -> CHECKPOINT -> RECEIPT`

Each message has a deterministic SHA-256. Duplicate message IDs with different content fail closed. Optional peer HMAC authentication is supported without placing secrets in model prompts.

## Meetings

BITOS can open a durable meeting containing roles such as Coordinator, Architect, Security, Verifier, Research and Executor. Workers may reason and challenge independently. Results are merged only after evidence and verifier/security gates.

## Network model

The HUB binds to loopback by default. Remote federation should use a private overlay such as Tailscale/WireGuard or an equivalent authenticated network. Do not expose the tool executor directly to the public Internet. Promote remote access only after a private peer address, peer authentication and HELLO/ACK tests are verified.

## Capacity model

A logical 10-agent fabric does not imply ten independent quota pools. Provider capacity is measured, grouped and rate-limit aware. `WAITING_MODEL`, `QUOTA_WAIT`, `CONTEXT_PRESSURE` and `OFFLINE` are separate states. The Coordinator keeps durable work queued when model capacity disappears.

## Expansion path

1. WebAgentWorker V2.2: transactional browser worker with lease, reconciliation and receipts.
2. Context successor automation: checkpoint before context exhaustion, open successor chat, resume capsule, no replay.
3. Provider adapters: OpenCode, browser LLMs and legitimately authorized APIs/models.
4. Private federation overlay: BITOS HUB plus VPS/desktop workers.
5. Research miner: GitHub/docs ingestion, deduplication, evidence scoring and deliberation.
6. Release/funding: reproducible packages, checksums, demos, support/donation links and paid deployment/support services.

## Invariants

No credential harvesting, no provider-private endpoint dependence, no quota/auth/payment bypass, no automatic account rotation to evade limits, no automatic spending, and no unauthenticated public execution surface.
