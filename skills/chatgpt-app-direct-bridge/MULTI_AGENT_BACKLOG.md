# First Direct Multi-Agent Specter Backlog

This backlog was produced by two existing ChatGPT chats communicating through the local App direct bridge, without browser automation. Agent A acted as Architect; Agent B acted as Verifier / Execution Planner. No host mutations were delegated to the agents during the session.

## M1 — Durable Mission State Contract

Owner role: Architect / Agent A. Priority: P0.

Make Durable Mission State the operational source of truth. Context Lineage is for continuity, branching, compaction, and context reconstruction, not authority.

Minimum state should include mission/task identity, immutable TaskSpec hash/version, role assignment, state, attempts, lease/fencing, idempotency key, evidence refs, effect status, verifier status, checkpoint/lineage, timestamps, and schema version.

Promotion tests:

- survive restart without chat history;
- replay does not duplicate a logical effect;
- stale fencing cannot commit;
- context can be rebuilt without changing mission state;
- schema migration preserves evidence;
- `EFFECT_UNKNOWN` blocks automatic retry.

Target: 100% mission recovery in crash/replay tests and zero logical duplicates.

## M2 — Reliability Kernel + Effect Reconciliation

Owner role: Architect / Agent A. Priority: P0/P1. Depends on M1.

Implement durable job state, leases/fencing, idempotency, journal recovery, explicit waiting states, and effect reconciliation.

Required effect classes:

- `EFFECT_CONFIRMED`
- `NO_EFFECT`
- `EFFECT_UNKNOWN`

Only `NO_EFFECT` may retry automatically. `EFFECT_UNKNOWN` must enter reconciliation before completion, compensation, retry, or escalation.

Target: zero duplicate effects in fault injection; deterministic recovery for known states; zero blind retry after ambiguous effects.

## M3 — Independent Critical Verifier

Owner role: Verifier / Agent B. Priority: P0 for HIGH/CRITICAL. Depends on M1 and M2.

HIGH/CRITICAL tasks require logically independent verification using the original TaskSpec, receipts, hashes, artifacts, execution metadata, and predefined invariants.

Verifier outputs: `PASS`, `FAIL`, or `INCONCLUSIVE`, plus evidence references and failed invariants.

Target: all injected HIGH/CRITICAL semantic failures are detected before `ATTAINED`; zero promotion based only on executor text.

## M4 — Adaptive Agent Selection v1

Owner role: Verifier / Planner / Agent B. Priority: P1/P2. Depends on M1–M3.

Keep logical roles stable (`Planner`, `Executor`, `Verifier`, `Governance`) while selecting the concrete agent dynamically.

Hard gates occur before scoring:

`security -> policy -> capability -> environment -> health -> availability -> ranking`

Use multidimensional `AgentCapabilityProfile` rather than one global score. Include verified success, critical failure rate, latency, resource cost, tool reliability, context fit, freshness, and verification quality.

Initial measurable goal: at least 20% fewer retries and 15% lower median PLAN-to-VERIFIED latency with zero increase in critical false-PASS regression tests.

## Promotion order

`M1 Durable State -> M2 Reliability/Reconciliation -> M3 Independent Verification -> M4 Adaptive Selection`

Final promotion gate:

- crash/replay safety;
- zero blind retries;
- zero critical false-PASS;
- measurable improvement in verified-task retries and latency.
