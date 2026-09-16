# Runtime AI bridge

The WorldMind NPC layer is always-on and deterministic. Optional LLM dialogue is server-side only.

Suggested keyless OpenCode configuration when operator accepts its service terms:

NPC_AI_BASE_URL=https://opencode.ai/zen/v1
NPC_AI_MODEL=muse-spark-1.3-contributor-free

No browser code receives provider credentials. If the provider times out or rejects a request, dialogue falls back immediately while movement, memory, resource gathering and building continue.

Hermes can also be used by pointing NPC_AI_BASE_URL to an authorized OpenAI-compatible Hermes gateway.
