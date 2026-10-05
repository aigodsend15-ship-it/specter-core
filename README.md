# ⚡ SPECTER CORE (v5.1.0 — SOVEREIGN AGENT MESH)

> **Autonomous Multi-Agent Federation Gateway, Interactive PowerShell Terminal, Model Context Protocol (MCP) Hub & Deterministic Broker.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Architecture: Sovereign](https://img.shields.io/badge/Architecture-Sovereign%20Pull--Only-green.svg)]()
[![MCP: Supported](https://img.shields.io/badge/MCP-Protocol%20Ready-purple.svg)](https://modelcontextprotocol.io/)
[![Zero-Leak Guarantee](https://img.shields.io/badge/Security-Zero%20Host%20Leakage-success.svg)]()

**Creator & Lead Architect:** Guilherme Peralta Novaes  
**Official Repository:** [https://github.com/aigodsend15-ship-it/specter-core](https://github.com/aigodsend15-ship-it/specter-core)

---

## 🌟 Overview

**Specter Core** is an ultra-lightweight, resilient, and sovereign multi-agent coordination platform designed to bridge autonomous AI agents (ChatGPT, Grok, Claude, local models, and custom micro-agents) without central lock-in, recurring cloud fees, or exposing private host resources to the public internet.

Specter operates under a **Strict Sovereign Isolation Invariant**:
- **Zero Host Exposure:** External agents interact exclusively via explicit deterministic REST/JSON-RPC gateways (`/api/message`, `/v1/federation/submit_code`, and MCP stdio).
- **No Credentials Leaked:** All tunnels, authtokens, keystores, and file systems are isolated behind local `.env` guards and sanitized environments.
- **Ultra-Low Resource Footprint:** `<0.1% CPU` idle utilization, zero redundant polling spam, and SQLite WAL ACID persistence.

---

## 🏛️ Core Architectural Pillars

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                 EXTERNAL AGENT SWARM                    │
                  │   (Grok, Claude, ChatGPT, OpenCode, Remote Bots)       │
                  └───────────────────────────┬─────────────────────────────┘
                                              │ Secure HTTPS Tunnel / MCP
                                              ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ SPECTER SOVEREIGN GATEWAY (Local Node — Port 8888 / 8080)                                │
├───────────────────────────────┬──────────────────────────┬───────────────────────────────┤
│ 🎮 POWERSHELL INTERACTIVE HUB │ ⚡ MODEL CONTEXT PROTOCOL │ 🌐 OPENAI-COMPATIBLE GATEWAY  │
│   - specter_terminal.py       │   - specter_mcp_server.py│   - unified_inference_gateway │
│   - Live ANSI session UI      │   - JSON-RPC 2.0 Stdio   │   - POST /v1/chat/completions │
│   - Atomic .txt session logs  │   - Claude Desktop Ready │   - SSE Streaming & Fallback  │
├───────────────────────────────┴──────────────────────────┴───────────────────────────────┤
│ 🛡️ SOVEREIGN BROKER & DETERMINISTIC DISPATCHER                                            │
│   - SQLite WAL Event Ledger (PRAGMA journal_mode=WAL; BEGIN IMMEDIATE)                   │
│   - SHA-256 Proof-of-Execution Receipts & Exchange Isolation                             │
│   - Pull-Only Topologies & Zero Internal Host Leaks                                      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 🧠 Sovereign Episodic Memory Engine (`Core/specter_memory_engine.py`)
- **Tripartite Capability Security**: Decouples **Memory Records** (content-addressed SHA-256), **Authority Capabilities** (unforgeable HMAC tokens), and **Retrieval Receipts** (atomic Merkle proofs) following the formal seL4 / Dennis & Van Horn model.
- **Microsecond Deterministic Retrieval**: Pure-Python Okapi BM25 ($k_1=1.5, b=0.75$) + Trigram inverted index over SQLite WAL — zero bloated vector DB or LangChain overhead.
- **$O(1)$ Tombstone Revocation**: Instant cryptographic purge of revoked capabilities prior to ranking, guaranteeing mathematical non-interference.
- **Merkle State Commitment**: Emits unforgeable inclusion proofs `verify_proof(leaf, path, root)` verifying exact context integrity at the IPC boundary.


1. **PowerShell Terminal Hub v5.1 (`specter-terminal`)**:
   - Interactive terminal console running directly in PowerShell or Linux bash.
   - Real-time session logger writing readable, audit-ready `.txt` transcript files (`History/Sessions/session_YYYY-MM-DD_HHMMSS.txt`).
   - Dynamic tunnel broadcasting (`/tunnel`), agent network health checks (`/nodes`), and zero-latency chat with external chatbots.

2. **Official Model Context Protocol (MCP) Server (`specter-mcp`)**:
   - Native JSON-RPC 2.0 Stdio implementation complying with Anthropic's MCP specification.
   - Instant drop-in tools for Claude Desktop, Cursor, and VS Code:
     - `specter_submit_code`: Cryptographically verified file ingestion with SHA-256 digests.
     - `specter_send_message`: Direct messaging into the swarm dialogue bus.
     - `specter_get_dialogue`: Reads latest swarm consensus turns.
     - `specter_get_nodes`: Real-time node telemetry and mesh status.
     - `specter_calculate_rsi`: Momentum calculation for decentralized nodes and telemetry metrics.

3. **OpenAI-Compatible Inference Gateway (`specter-gateway`)**:
   - Pure Python standard library HTTP/1.1 server (`POST /v1/chat/completions`, `GET /v1/models`, `GET /health`).
   - Bidirectional Server-Sent Events (SSE) streaming with circuit breaker failover.

4. **Task Dispatcher & Auto-Healer (`specter-dispatcher`)**:
   - 24/7 background supervisor monitoring tasks, isolating working directories, and generating deterministic receipts.

---

## 🚀 Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/aigodsend15-ship-it/specter-core.git
cd specter-core

# Install in editable mode
pip install -e .
```

Or copy `.env.example` to `.env` to configure optional custom ports and upstream keys:
```bash
cp .env.example .env
```

---

### 2. Launching the Interactive PowerShell Hub

On Windows (PowerShell):
```powershell
python Core/specter_terminal.py
```
Or use the pre-configured script:
```powershell
.\Core\specter_console.ps1
```

**Commands inside the console:**
- `/tunnel` — Display active public tunnel URL to share with chatbots.
- `/nodes` — List online agents in the mesh.
- `/history` — Show latest turns from the current session.
- `/log` — Open the session `.txt` log in the default text editor.
- `/exit` — Cleanly close the console while preserving logs.
- `@AgentName: message` — Direct a message to a specific agent (e.g. `@Grok: build test harness`).

---

### 3. Integrating with Claude Desktop / Cursor (MCP)

Add Specter to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "specter": {
      "command": "python",
      "args": ["-m", "Core.specter_mcp_server"],
      "cwd": "C:/path/to/specter-core"
    }
  }
}
```

Now Claude Desktop can natively query Specter, dispatch tasks, and ingest verified code blocks.

---

### 4. Running the 24/7 Resilient Gateway & Supervisor

```bash
# Start the OpenAI-compatible gateway
specter-gateway --host 127.0.0.1 --port 8080 --mock

# Start the background task supervisor
specter-supervisor
```

### 5. Running via Docker

```bash
docker-compose up -d
```

---

## 🧪 Automated Testing

Specter maintains high test coverage with zero external mocking requirements:

```bash
pytest Core/ -v
```

All core tests execute within milliseconds against in-memory or WAL SQLite fixtures.

---

## 🔒 Security & Sovereign Guarantee

Specter was built under the **Zero Internal Host Leakage** doctrine:
- **No private paths:** File operations default to user-defined directories or standard application folders.
- **No exposed credentials:** Authentication tokens, private keys, and tunnel auth codes remain strictly in local `.env` or keychain storage.
- **Deterministic provenance:** Every file ingested through `/v1/federation/submit_code` generates a SHA-256 cryptographic receipt recorded immutably in SQLite.

---

## 📜 License & Attribution

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for complete details.

**Author:** Guilherme Peralta Novaes (`aigodsend15@gmail.com`)  
**Community & Issues:** [GitHub Issues](https://github.com/aigodsend15-ship-it/specter-core/issues)
