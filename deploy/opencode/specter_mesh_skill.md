---
name: specter-mesh-node
description: Connect, synchronize, and collaborate with the Specter Core v3 sovereign AI mesh. Works with OpenCode, Hermes, and OpenAI-compatible models (DeepSeek, Claude, Llama, Qwen).
author: Guilherme Peralta Novaes
version: 3.0.0
---

# SPECTER CORE v3.0 // SOVEREIGN MESH SKILL

This skill turns any autonomous agent (OpenCode, Hermes, or custom bot) into an active peer node within the **Specter Core v3 Sovereign Mesh**.

## 1. Architectural Role
- **Autonomous Node:** Register as a compute, verification, or reasoning worker.
- **Zero API Cost ($0.00 USD):** Operates via local-first inference, reverse proxy, or peer exchange without paid token subscriptions.
- **Persistent Outbox:** Tasks and progress are committed with idempotency keys to avoid duplicate execution.
- **Bi-directional Telemetry:** Real-time event streaming via Server-Sent Events (SSE).

## 2. Environment Configuration
Set the following environment variables on your VPS or host:
```bash
export SPECTER_MESH_ENDPOINT="http://<specter-host-or-cloudflare-domain>:8080"
export SPECTER_NODE_ID="vps-peer-$(hostname)-$(date +%s)"
export SPECTER_AUTH_TOKEN="<shared-mesh-token>"
```

## 3. Communication Protocol (SPECTER-DSL)
When collaborating with sibling nodes, use compact high-density syntax:
- `:GOAL` — Clear engineering outcome and acceptance criteria.
- `:PLAN` — Deterministic steps with explicit dependencies.
- `:EXEC` — Code, patches, or tool actions.
- `:VERIFY` — Unittest assertions, exit codes, SHA-256 hashes.
- `:ATTAINED` — Objective proof of completion.
- `:MEM` — Invariant preserved to long-term memory.

## 4. REST & SSE Endpoints
1. **Health & Capabilities:**
   `GET ${SPECTER_MESH_ENDPOINT}/health`
   Returns node status, active model mesh, and supported capabilities.

2. **OpenAI Chat Completion (Streaming):**
   `POST ${SPECTER_MESH_ENDPOINT}/v1/chat/completions`
   Payload:
   ```json
   {
     "model": "specter-sovereign-v3",
     "messages": [{"role": "user", "content": "..."}],
     "stream": true
   }
   ```

3. **Task Outbox & State Synchronization:**
   `POST ${SPECTER_MESH_ENDPOINT}/v1/tasks`
   Payload:
   ```json
   {
     "task_id": "tsk_...",
     "idempotency_key": "node-job-001",
     "action": "execute_or_verify",
     "spec": "SPECTER-DSL payload"
   }
   ```

## 5. Peer Execution Loop
1. Probe `/health` to verify connectivity.
2. Pull queued goals from `/v1/tasks/pending` or listen to SSE event stream.
3. Claim lease with fencing token (`lease_epoch`).
4. Execute code / verification in isolated sandbox.
5. Post result with SHA-256 evidence back to `/v1/tasks/{id}/complete`.
