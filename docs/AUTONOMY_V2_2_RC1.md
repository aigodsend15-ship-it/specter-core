# Specter Autonomy V2.2 Federation RC1

Status: release candidate validated offline on BITOS; not a provider-quota bypass and not an unauthenticated remote-control surface.

## Canonical implementation

The RC1 implementation lives in `Core/autonomy_v2_2/`. The release-gate suite is in `Core/tests_autonomy_v2_2/`.

The older `Core/federation_hub_v22.py` remains as a legacy bootstrap/reference while RC1 is reviewed. New federation work should target the canonical package above.

## What changed

- Atomic durable task claims with SQLite `BEGIN IMMEDIATE`, fencing tokens and conditional state/owner/token updates.
- Task state transition and audit event are committed in the same transaction.
- Browser/Web-LLM dispatches persist task, operation, endpoint, conversation, worker and authorization linkage.
- Restart reconciliation occurs before any possible resend; timeout is treated as uncertain effect.
- Local GovernanceGate binds authorization to operation, capability, canonical argument hash, scope and policy hash.
- Signed receipts are bound to the authorization and canonical operation contract.
- SMP/1 envelopes are hashed canonically and authenticated peers must match the sender identity.
- Broadcast delivery acknowledgements are tracked independently per recipient.
- Divergent replay under the same message ID fails closed.
- Federation databases migrate additively without deleting unrelated legacy tables.
## Verified release gates

On 2026-09-10, Python 3.12 clean runtime compiled the canonical package and the RC1/V2/V2.1/V2.2 suite passed `25/25`.

Additional existing Core regression tests passed `18/18`, covering federation bootstrap, mesh broker concurrency/outbox recovery, execution-router fencing/receipts and autonomous-broker durability. Total executed in this promotion round: `43/43 PASS`.

Release-gate coverage includes 50 concurrent claim contenders with exactly one owner; stale fencing rejection after reclaim; restart reconciliation with zero duplicate resend; governance authorization/receipt binding; non-destructive DB migration; per-recipient broadcast ACK; and divergent replay rejection.

## Authority and trust model

Web/model output remains data, not authority. Host mutations require a local, verifiable authorization contract. Secrets, cookies and provider credentials do not belong in prompts or SMP/1 messages. Remote federation remains loopback/private-overlay first; unrestricted executor ports must not be exposed publicly.

## Promotion model

This RC1 is suitable for branch publication and further review. Promotion into the active BITOS runtime should remain reversible and use a fresh state backup/checkpoint. Provider capacity is independent of agent count; `QUOTA_WAIT` and rate limits are respected rather than bypassed.
