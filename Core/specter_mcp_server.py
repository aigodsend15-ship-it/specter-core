"""
====================================================================
SPECTER MCP SERVER | FEDERATION MODEL CONTEXT PROTOCOL HUB
====================================================================
Autoridade: L0 Sovereign Owner (Guilherme) / Specter Federation Mesh
Protocolo: Model Context Protocol (MCP) 2024-11-05 via JSON-RPC 2.0 Stdio
Porta Core: 8888 (http://127.0.0.1:8888)
Armazenamento: C:\\Specter\\Exchange\\incoming | C:\\Specter\\Core\\storage\\specter_fabric.sqlite3

Tools Expostas:
  1. specter_submit_code: Ingestão de arquivos de código com SHA256 na malha
  2. specter_send_message: Publicação direta de mensagens no chat da federação
  3. specter_get_dialogue: Leitura dos últimos turnos de diálogo do swarm
  4. specter_get_nodes: Listagem e status de saúde dos nós da malha
  5. specter_calculate_rsi: Cálculo de momentum RSI para ativos/tokens DePIN
====================================================================
"""

import sys
import os
import time
import json
import uuid
import hashlib
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional

# Garantir UTF-8 sem corrupção no Windows Stdio
if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mcp.server.mcpserver import MCPServer

# --- CONSTANTES & DIRETÓRIOS ---
CORE_HTTP_URL = os.getenv("SPECTER_GATEWAY_URL", "http://127.0.0.1:8888")
ROOT_DIR = Path(os.getenv("SPECTER_ROOT", Path(__file__).resolve().parent.parent))
CORE_DIR = ROOT_DIR / "Core"
CONTROL_DIR = ROOT_DIR / "Control"
STATE_DIR = CONTROL_DIR / "state"
STORAGE_DIR = CORE_DIR / "storage"
EXCHANGE_DIR = ROOT_DIR / "Exchange"
INCOMING_DIR = EXCHANGE_DIR / "incoming"

STATE_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = STORAGE_DIR / "specter_fabric.sqlite3"
REMOTE_CHAT_FILE = STATE_DIR / "remote_chat.jsonl"

# --- INSTÂNCIA MCP SERVER ---
mcp = MCPServer(
    "specter-federation-mcp",
    version="1.0.0",
    instructions=(
        "Specter Federation MCP Server. Servidor oficial do ecossistema Specter para "
        "comunicação entre agentes (Swarm), envio de códigos e propostas via Exchange com SHA256, "
        "monitoramento da malha de nós federados e cálculo analítico de momentum RSI para DePIN."
    )
)


# --- UTILITÁRIOS RESILIENTES (HTTP COM RETRY / DB FALLBACK) ---
def _http_get(endpoint: str, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    try:
        url = f"{CORE_HTTP_URL}{endpoint}"
        req = urllib.request.Request(url, headers={"User-Agent": "Specter-MCP/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
    except Exception:
        pass
    return None


def _http_post(endpoint: str, payload: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    try:
        url = f"{CORE_HTTP_URL}{endpoint}"
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Specter-MCP/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 201):
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
    except Exception:
        pass
    return None


def _db_connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=15000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def _format_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ====================================================================
# TOOL 1: specter_submit_code
# ====================================================================
@mcp.tool()
def specter_submit_code(filename: str, code: str, description: str = "") -> str:
    """Envia arquivos de código para ingestão na malha Specter (C:\\Specter\\Exchange\\incoming) com hash SHA256 e anúncio no swarm.

    Args:
        filename: Nome do arquivo (ex: 'depin_node_worker.py', 'optimizer.go').
        code: Conteúdo de código completo do arquivo.
        description: Breve descrição ou finalidade do código submetido.

    Returns:
        JSON com confirmação de recebimento, caminho salvo, SHA256 e status.
    """
    if not filename or not filename.strip():
        return json.dumps({"ok": False, "error": "Nome do arquivo (filename) não pode ser vazio."}, ensure_ascii=False)

    safe_filename = Path(filename.strip()).name
    agent_id = "MCP_External_Agent"
    
    # 1. Tentar endpoint HTTP oficial ativo na porta 8888
    http_payload = {
        "agent_id": agent_id,
        "filename": safe_filename,
        "code": code,
        "description": description,
        "target_dir": "Exchange/incoming",
        "platform": "MCP_Client"
    }
    resp = _http_post("/v1/federation/submit_code", http_payload, timeout=5.0)
    if resp and resp.get("ok"):
        return json.dumps(resp, ensure_ascii=False, indent=2)

    # 2. Fallback resiliente: escrita direta no Exchange e banco de dados SQLite WAL
    now_ts = time.time()
    code_bytes = code.encode("utf-8") if isinstance(code, str) else bytes(code)
    size_bytes = len(code_bytes)
    sha256_hash = hashlib.sha256(code_bytes).hexdigest()
    timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(now_ts))
    saved_filename = f"{timestamp_str}_{sha256_hash[:8]}_{safe_filename}"
    target_path = INCOMING_DIR / saved_filename

    try:
        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(code_bytes)

        # Gravar sidecar .meta.json
        meta_path = INCOMING_DIR / f"{saved_filename}.meta.json"
        meta_info = {
            "agent_id": agent_id,
            "filename": safe_filename,
            "saved_filename": saved_filename,
            "saved_path": str(target_path),
            "size_bytes": size_bytes,
            "hash_digest": sha256_hash,
            "target_dir": "Exchange/incoming",
            "description": description,
            "timestamp": now_ts,
            "formatted_time": _format_time(now_ts),
            "ingestion_method": "MCP_DIRECT_FALLBACK"
        }
        meta_path.write_text(json.dumps(meta_info, indent=2, ensure_ascii=False), encoding="utf-8")

        # Registro no SQLite WAL
        try:
            conn = _db_connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS federation_code_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    agent_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    saved_path TEXT NOT NULL,
                    hash_digest TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    target_dir TEXT,
                    description TEXT
                );
            """)
            conn.execute("""
                INSERT INTO federation_code_submissions (timestamp, agent_id, filename, saved_path, hash_digest, size_bytes, target_dir, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (now_ts, agent_id, safe_filename, str(target_path), sha256_hash, size_bytes, "Exchange/incoming", description))
            
            # Anúncio no swarm_dialogue_ledger
            announcement = f"🌐 [EXTERNAL_CODE_SUBMIT] {agent_id} enviou {safe_filename} ({size_bytes} bytes)\n📝 Descrição: {description}\n📁 Salvo em: Exchange/incoming/{saved_filename}\n🔐 SHA256: {sha256_hash}"
            digest = f"sha256:{hashlib.sha256((agent_id + announcement).encode('utf-8')).hexdigest()[:16]}"
            conn.execute("""
                INSERT INTO swarm_dialogue_ledger (timestamp, sender, receiver, dsl_message, hash_digest)
                VALUES (?, ?, ?, ?, ?)
            """, (now_ts, agent_id, "TODOS_AGENTES", announcement, digest))
            conn.close()
        except Exception:
            pass

        # Anúncio no remote_chat.jsonl
        try:
            with open(REMOTE_CHAT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "sender": agent_id,
                    "receiver": "TODOS_AGENTES",
                    "message": f"🌐 [EXTERNAL_CODE_SUBMIT] {safe_filename} ({size_bytes} bytes) | SHA256: {sha256_hash}",
                    "timestamp": now_ts
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return json.dumps({
            "ok": True,
            "status": "ACCEPTED",
            "agent_id": agent_id,
            "filename": safe_filename,
            "saved_filename": saved_filename,
            "saved_path": str(target_path),
            "hash_digest": sha256_hash,
            "size_bytes": size_bytes,
            "timestamp": now_ts,
            "formatted_time": _format_time(now_ts),
            "announcement": f"Código submetido com sucesso via MCP para {target_path}",
            "transport": "direct_exchange_fallback"
        }, ensure_ascii=False, indent=2)

    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao salvar código: {str(exc)}"}, ensure_ascii=False)


# ====================================================================
# TOOL 2: specter_send_message
# ====================================================================
@mcp.tool()
def specter_send_message(sender: str, text: str, receiver: str = "TODOS_AGENTES") -> str:
    """Publica uma mensagem diretamente no chat em tempo real da Federação Specter (porta 8888).

    Args:
        sender: Identificação do agente remetente (ex: 'Claude_Desktop', 'Cursor_Agent', 'Grok_Scout').
        text: Conteúdo textual da mensagem ou instrução.
        receiver: Destinatário específico (ex: '⚡ HERMES (Muse Spark 1.3)', 'OWNER (Guilherme)') ou 'TODOS_AGENTES'.

    Returns:
        JSON com recibo da mensagem, turn_id e status de confirmação.
    """
    if not text or not text.strip():
        return json.dumps({"ok": False, "error": "Texto da mensagem não pode ser vazio."}, ensure_ascii=False)
    
    sender_clean = sender.strip() if sender and sender.strip() else "MCP_Agent"
    receiver_clean = receiver.strip() if receiver and receiver.strip() else "TODOS_AGENTES"
    text_clean = text.strip()

    # 1. Tentar endpoint HTTP /api/message
    post_data = {
        "sender": sender_clean,
        "receiver": receiver_clean,
        "text": text_clean,
        "message_id": f"mcp_{uuid.uuid4().hex[:12]}",
        "platform": "MCP",
        "role": "MCP Federated Agent"
    }
    resp = _http_post("/api/message", post_data, timeout=5.0)
    if resp and resp.get("ok"):
        return json.dumps(resp, ensure_ascii=False, indent=2)

    # 2. Fallback direto via SQLite WAL e remote_chat.jsonl
    now_ts = time.time()
    try:
        conn = _db_connect()
        digest = f"sha256:{hashlib.sha256((sender_clean + text_clean).encode('utf-8')).hexdigest()[:16]}"
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO swarm_dialogue_ledger (timestamp, sender, receiver, dsl_message, hash_digest)
            VALUES (?, ?, ?, ?, ?)
        """, (now_ts, sender_clean, receiver_clean, text_clean, digest))
        turn_id = cur.lastrowid
        conn.close()

        with open(REMOTE_CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "sender": sender_clean,
                "receiver": receiver_clean,
                "message": text_clean,
                "timestamp": now_ts
            }, ensure_ascii=False) + "\n")

        return json.dumps({
            "ok": True,
            "status": "DELIVERED_FALLBACK",
            "turn_id": turn_id,
            "timestamp": now_ts,
            "formatted_time": _format_time(now_ts),
            "sender": sender_clean,
            "receiver": receiver_clean,
            "text": text_clean
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao entregar mensagem: {str(exc)}"}, ensure_ascii=False)


# ====================================================================
# TOOL 3: specter_get_dialogue
# ====================================================================
@mcp.tool()
def specter_get_dialogue(limit: int = 20) -> str:
    """Lê os últimos turnos de mensagens do chat e do swarm da Federação Specter.

    Args:
        limit: Quantidade máxima de mensagens recentes a retornar (padrão 20, máximo 100).

    Returns:
        JSON com a lista cronológica das últimas mensagens trocadas pelos agentes.
    """
    lim = max(1, min(int(limit), 100))

    # 1. Tentar endpoint HTTP /api/state
    state = _http_get("/api/state", timeout=4.0)
    if state and "dialogue" in state:
        dialogue = state["dialogue"]
        recent = dialogue[-lim:] if len(dialogue) > lim else dialogue
        return json.dumps({
            "ok": True,
            "count": len(recent),
            "total_available": len(dialogue),
            "messages": recent
        }, ensure_ascii=False, indent=2)

    # 2. Fallback direto via SQLite
    try:
        conn = _db_connect()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT turn_id, timestamp, sender, receiver, dsl_message
            FROM swarm_dialogue_ledger
            ORDER BY turn_id DESC
            LIMIT ?
        """, (lim,)).fetchall()
        conn.close()

        messages = []
        for r in reversed(rows):
            messages.append({
                "id": r["turn_id"],
                "time": _format_time(r["timestamp"]),
                "sender": r["sender"],
                "receiver": r["receiver"],
                "text": r["dsl_message"]
            })

        return json.dumps({
            "ok": True,
            "count": len(messages),
            "source": "sqlite_fabric_direct",
            "messages": messages
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Erro ao consultar diálogo: {str(exc)}"}, ensure_ascii=False)


# ====================================================================
# TOOL 4: specter_get_nodes
# ====================================================================
@mcp.tool()
def specter_get_nodes() -> str:
    """Lista todos os nós ativos, agentes federados e o status de saúde da malha Specter.

    Returns:
        JSON com a lista de nós ONLINE, papéis, plataformas, tempo ativo e agentes federados.
    """
    # 1. Tentar endpoint HTTP /api/state
    state = _http_get("/api/state", timeout=4.0)
    if state and state.get("ok"):
        return json.dumps({
            "ok": True,
            "uptime_seconds": state.get("uptime_seconds", 0),
            "active_nodes_count": len(state.get("nodes", [])),
            "nodes": state.get("nodes", []),
            "federation_agents": state.get("federation_agents", [])
        }, ensure_ascii=False, indent=2)

    # 2. Fallback direto via SQLite
    try:
        conn = _db_connect()
        conn.row_factory = sqlite3.Row
        nodes = []
        for n in conn.execute("""
            SELECT node_id, display_name, role, platform, account, last_seen, status
            FROM hub_active_nodes
            WHERE status = 'ONLINE'
            ORDER BY last_seen DESC
        """).fetchall():
            nodes.append({
                "node_id": n["node_id"],
                "display_name": n["display_name"],
                "role": n["role"],
                "platform": n["platform"],
                "account": n["account"],
                "last_seen_time": _format_time(n["last_seen"]),
                "status": n["status"]
            })

        agents = []
        for f in conn.execute("SELECT agent_id, name, model, capabilities, messages_sent FROM federation_agents").fetchall():
            agents.append({
                "agent_id": f["agent_id"],
                "name": f["name"],
                "model": f["model"],
                "messages_sent": f["messages_sent"]
            })
        conn.close()

        return json.dumps({
            "ok": True,
            "source": "sqlite_fabric_direct",
            "active_nodes_count": len(nodes),
            "nodes": nodes,
            "federation_agents": agents
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Erro ao consultar nós: {str(exc)}"}, ensure_ascii=False)


# ====================================================================
# TOOL 5: specter_calculate_rsi
# ====================================================================
@mcp.tool()
def specter_calculate_rsi(prices: List[float]) -> str:
    """Executa cálculos analíticos de momentum RSI (Relative Strength Index) para ativos ou tokens DePIN.

    Args:
        prices: Lista ordenada cronologicamente de preços de fechamento (float). Requer ao menos 2 preços.

    Returns:
        JSON detalhado com o valor do RSI (0 a 100), sinal (OVERBOUGHT, OVERSOLD, NEUTRAL),
        variação percentual, extremos de preço e diagnóstico interpretativo.
    """
    if not prices or not isinstance(prices, list):
        return json.dumps({
            "ok": False,
            "error": "O parâmetro 'prices' deve ser uma lista de números decimais/inteiros."
        }, ensure_ascii=False)

    try:
        clean_prices = [float(p) for p in prices]
    except (ValueError, TypeError) as exc:
        return json.dumps({
            "ok": False,
            "error": f"Elementos inválidos na série de preços: {str(exc)}"
        }, ensure_ascii=False)

    n = len(clean_prices)
    if n < 2:
        return json.dumps({
            "ok": False,
            "error": f"A série contém apenas {n} preço(s). São necessários ao menos 2 preços para calcular o RSI."
        }, ensure_ascii=False)

    deltas = [clean_prices[i] - clean_prices[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    period = 14
    eff_period = min(period, len(deltas))

    # Médias iniciais
    avg_gain = sum(gains[:eff_period]) / eff_period
    avg_loss = sum(losses[:eff_period]) / eff_period

    # Suavização de Wilder (Wilder's Smoothing)
    for i in range(eff_period, len(deltas)):
        avg_gain = (avg_gain * (eff_period - 1) + gains[i]) / eff_period
        avg_loss = (avg_loss * (eff_period - 1) + losses[i]) / eff_period

    if avg_loss == 0.0:
        rsi = 100.0 if avg_gain > 0 else 50.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi_rounded = round(rsi, 2)

    if rsi_rounded >= 70.0:
        signal = "OVERBOUGHT"
        interpretation = "Ativo sobrecomprado: momentum altista esticado. Possível exaustão compradora ou retração iminente."
    elif rsi_rounded <= 30.0:
        signal = "OVERSOLD"
        interpretation = "Ativo sobrevendido: momentum baixista esticado. Potencial de repique técnico ou acumulação estratégica."
    else:
        signal = "NEUTRAL"
        interpretation = "Zona neutra de momentum: tendência em equilíbrio dinâmico, sem sobrecompra ou sobrevenda severa."

    price_start = clean_prices[0]
    price_current = clean_prices[-1]
    net_diff = price_current - price_start
    pct_diff = round((net_diff / price_start) * 100, 2) if price_start != 0 else 0.0

    result = {
        "ok": True,
        "rsi": rsi_rounded,
        "signal": signal,
        "interpretation": interpretation,
        "parameters": {
            "period_configured": period,
            "period_effective": eff_period,
            "total_points": n,
            "smoothing": "Wilder"
        },
        "price_summary": {
            "initial": price_start,
            "current": price_current,
            "highest": max(clean_prices),
            "lowest": min(clean_prices),
            "net_change": round(net_diff, 4),
            "net_change_pct": f"{pct_diff:+}%"
        },
        "averages": {
            "avg_gain": round(avg_gain, 6),
            "avg_loss": round(avg_loss, 6)
        }
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


# ====================================================================
# ENTRYPOINT DO SERVIDOR MCP
# ====================================================================
if __name__ == "__main__":
    mcp.run(transport="stdio")
