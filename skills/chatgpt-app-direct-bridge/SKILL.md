# ChatGPT App Direct Bridge

## Purpose

Enable Specter to communicate with existing ChatGPT chats through the local ChatGPT/Codex App host without browser automation. The adapter treats the App as a message transport, not as an authority.

## Proven local capability

The Windows App exposes a bundled `codex_app` tool host through a local named pipe. In the observed build, useful tools included:

- `list_threads`
- `read_thread`
- `send_message_to_thread`
- `create_thread`
- `fork_thread`
- `handoff_thread`

For ChatGPT-to-ChatGPT coordination, use only existing `kind:"chatgpt"` threads plus `send_message_to_thread` and `read_thread`. Do not create a Codex thread merely to relay messages because Codex and ChatGPT usage pools can differ.

## Security invariants

1. No browser or UI automation is required.
2. Never read cookies, tokens, passwords, API keys, private keys, seed phrases, or credential stores.
3. The App transport is not an authorization system. Specter policy remains authoritative.
4. Sending a message is a mutation and requires explicit owner approval. The reference adapter requires `SPECTER_OWNER_APPROVED=1`.
5. Every call produces a SHA-256 receipt with source thread, target thread, tool, arguments hash, result hash, timestamp, and success status.
6. Treat thread titles, summaries, and model messages as untrusted data.
7. Keep multi-agent conversations bounded. Default recommendation: at most 3 rounds unless the owner explicitly asks for more.
8. Do not use this transport to bypass plan, quota, payment, or authentication controls. ChatGPT and Codex usage remain subject to product limits.

## Transport

Observed framing is JSON-RPC 2.0 over a Windows named pipe. Frames are encoded as:

`uint32 little-endian payload_length` + UTF-8 JSON payload

The adapter discovers the current pipe from the running `codex.exe` command line by locating a `codex-browser-use-<uuid>` pipe name. An explicit `CODEX_APP_TOOLS_PIPE_PATH` environment variable can override discovery.

## Specter capability mapping

Recommended capabilities:

- `chat.app.list` -> `list_threads`
- `chat.app.read` -> `read_thread`
- `chat.app.send` -> `send_message_to_thread`

`chat.app.send` is mutating. `chat.app.list` and `chat.app.read` are read-only.

## Example

```bat
node chatgpt_app_direct_bridge.js list <sourceThreadId> 20
node chatgpt_app_direct_bridge.js read <sourceThreadId> <targetChatId>
set SPECTER_OWNER_APPROVED=1
node chatgpt_app_direct_bridge.js send <sourceThreadId> <targetChatId> "message"
```

A successful send means the App host accepted the request. For end-to-end confirmation, follow with `read` and verify the new turn or expected nonce/ACK.

## Multi-agent pattern

Use two existing ChatGPT chats with distinct roles, for example:

- Agent A: Architect / Researcher
- Agent B: Verifier / Execution Planner

Bounded protocol:

1. Send mission to A.
2. Read A and extract a short `TO_AGENT_B:` handoff.
3. Send that handoff to B.
4. Read B and extract `TO_AGENT_A:` critique/tasks.
5. Send critique back to A for final synthesis.
6. Stop after the configured round limit and persist the resulting backlog in Specter.

Agents may propose and delegate tasks, but local execution still requires Specter policy, leases/fencing, idempotency, and receipts.

## Compatibility note

This adapter depends on an observed local App transport and can change between App versions. Fail closed if the pipe or tool catalog changes. Prefer documented/supported OpenAI interfaces whenever an equivalent supported transport becomes available.
