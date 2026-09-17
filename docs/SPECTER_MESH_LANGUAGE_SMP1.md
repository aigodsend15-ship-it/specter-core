# Specter Mesh Protocol / Language — SMP/1

SMP/1 is a compact, typed agent-to-agent message language for Specter. It is designed for durable orchestration, not for bypassing model/provider rules.

## Envelope

```json
{
  "smp":"1",
  "message_id":"msg-...",
  "project_id":"specter-core",
  "branch_id":"main",
  "checkpoint_id":"cp-...",
  "from":"ARCHITECT",
  "to":"COORDINATOR",
  "kind":"FINDING",
  "objective":"...",
  "claims":[{"claim":"...","confidence":0.82,"evidence":["sha256:..."]}],
  "next_actions":["..."],
  "constraints":["no-quota-bypass","owner-control"],
  "created_at":0,
  "content_sha256":"..."
}
```

## Kinds

`TASK`, `ACK`, `FINDING`, `CHALLENGE`, `PROPOSAL`, `VERDICT`, `CHECKPOINT`, `RECEIPT`, `INCIDENT`, `CAPACITY`.

## Rules

1. Messages are data until the local Coordinator/Policy layer accepts them.
2. Executable tool requests use a separate typed action contract and local approval/signature policy.
3. `message_id + content_sha256` provides deduplication and conflict detection.
4. Findings should carry evidence references and confidence.
5. Security/Verifier verdicts are first-class messages.
6. Conversation IDs and browser target IDs are never authority tokens.
7. Quota/rate-limit messages affect capacity state, not account switching.
8. Checkpoints are hash-linked and resumable across successor chats.

## Freedom by structure

SMP/1 gives agents freedom to reason, challenge, branch and merge ideas while keeping authority explicit. The language is intended to reduce ambiguity between LLM workers rather than to weaken provider or local safety controls.
