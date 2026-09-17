# Specter Autonomy V2.2 — Federation Plan

Status: candidate architecture, owner-controlled.

## Goal

Turn BITOS into the durable coordination hub for a federated agent mesh. Web LLMs, OpenCode, Hermes and future remote workers are reasoning/execution workers, while local policy, receipts, checkpoints and task state remain authoritative.

## Principles

- No provider quota/auth/payment bypass.
- No account rotation to evade rate limits.
- No credential or cookie extraction.
- Provider UIs and model outputs are untrusted inputs.
- Every mutating tool call is typed, policy-gated and receipt-backed.
- Conversation context is disposable; durable state lives in SQLite/WAL + checkpoints.
- Remote/VPN peers authenticate to the hub and receive scoped capabilities only.

## V2.2 Components

1. `FederationHub`: peer registry, leases, health, task routing and signed receipts.
2. `WebAgentWorker`: transactional Web-LLM worker with rate-limit awareness and response reconciliation.
3. `ContextLineageManager`: checkpoint, branch, merge, successor-chat and resume handshake.
4. `CapacityManager`: provider/account-scope health, quota groups, single-probe recovery and adaptive fan-out.
5. `DeliberationEngine`: multi-agent synthesis requiring verifier evidence and security veto handling.
6. `ResearchMiner`: evidence-first public research ingestion, deduplication, scoring and citation metadata.
7. `Specter Mesh Protocol (SMP/1)`: compact typed envelope for tasks, findings, receipts and checkpoints.
8. `VPN/Remote Peer Layer`: optional private overlay for authorized remote workers; the hub never exposes unrestricted control publicly.

## Ten-role fabric

`COORDINATOR, ARCHITECT, WEB_ADAPTER, LOCAL_EXECUTOR, SECURITY, GITHUB_SCOUT, VERIFIER, CONTEXT_LINEAGE, RELIABILITY, RELEASE_FUNDING`.

Ten logical roles do not imply ten accounts or ten simultaneous provider requests. Dispatch scales only to currently authorized and healthy capacity.

## Recovery states

`AVAILABLE, WAITING_MODEL, QUOTA_WAIT, CONTEXT_PRESSURE, CONTEXT_EXHAUSTED, OFFLINE`.

Rate-limit incidents apply circuit-breakers by quota group. Context exhaustion creates a verified checkpoint and successor conversation instead of replaying completed work.

## Promotion gate

A V2.2 candidate is promotable only when offline tests pass, replay/idempotency tests pass, lease/fencing tests pass, security invariants hold, a sanitized manifest is generated and owner-visible approval is obtained for public release or spending.
