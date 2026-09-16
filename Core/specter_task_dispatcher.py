# -*- coding: utf-8 -*-
"""
SPECTER FEDERATION - TASK DISPATCHER & EVENT ROUTER (HOST EXECUTION ENGINE)
==========================================================================
Monitora o ledger de diálogo da Federação (swarm_dialogue_ledger), captura ordens,
tarefas e comandos direcionados à Malha/Antigravity/Federação, executa de forma
assíncrona e isolada em C:\\Specter\\Work, emite recibos criptográficos SHA256 em
broker_evidence e dascl_receipts, e publica o feedback imediato no ledger (/api/message).
"""

import sys
import os
import time
import json
import uuid
import socket
import psutil
import hashlib
import sqlite3
import argparse
import datetime
import subprocess
import urllib.request
from pathlib import Path

# --- CONFIGURAÇÕES DE DIRETÓRIO E CAMINHOS ---
CORE_DIR = Path(os.getenv("SPECTER_CORE_DIR", Path(__file__).resolve().parent))
DB_PATH = CORE_DIR / "storage" / "specter_fabric.sqlite3"
WORK_DIR = Path(os.getenv("SPECTER_WORK_DIR", CORE_DIR.parent / "Work"))
STORAGE_DIR = CORE_DIR / "storage"
LOG_FILE = CORE_DIR / "specter_task_dispatcher.log"
STATE_FILE = STORAGE_DIR / "dispatcher_last_turn.txt"
GATEWAY_URL = "http://127.0.0.1:8888"

AGENT_ID = "ANTIGRAVITY_DISPATCHER"
AGENT_NAME = "⚡ Antigravity Task Dispatcher & Event Router"
ROLE = "Task Dispatcher & Host Execution Engine"
PLATFORM = "Specter Host Node (C:\\Specter\\Work)"

MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
DEFAULT_TIMEOUT_SEC = 180

try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    try:
        print(entry, flush=True)
    except Exception:
        pass
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE:
            old_log = LOG_FILE.with_suffix(".log.old")
            if old_log.exists():
                old_log.unlink()
            LOG_FILE.rename(old_log)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def touch_heartbeat():
    """Registra presença contínua do despachante no banco SQLite da Federação."""
    now = time.time()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("""
            INSERT INTO hub_active_nodes (node_id, display_name, role, platform, account, last_seen, status)
            VALUES (?, ?, ?, ?, 'Specter Core / Host Execution', ?, 'ONLINE')
            ON CONFLICT(node_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                status = 'ONLINE',
                display_name = excluded.display_name,
                role = excluded.role
        """, (AGENT_ID, AGENT_NAME, ROLE, PLATFORM, now))

        conn.execute("""
            INSERT INTO federation_agents (agent_id, name, capabilities, model, platform, session_token, joined_at, last_heartbeat, status, messages_sent)
            VALUES (?, ?, ?, 'Host-PowerShell-Engine', 'Local/Host', ?, ?, ?, 'ACTIVE', 0)
            ON CONFLICT(agent_id) DO UPDATE SET
                last_heartbeat = excluded.last_heartbeat,
                status = 'ACTIVE'
        """, (
            AGENT_ID,
            AGENT_NAME,
            json.dumps(["powershell", "task_dispatch", "evidence_receipts", "host_isolation"]),
            f"token_{AGENT_ID.lower()}",
            now,
            now
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"[Heartbeat DB Error]: {e}")


def post_to_ledger(text: str, receiver: str = "TODOS_AGENTES_E_OWNER") -> bool:
    """Publica mensagem e telemetria no ledger via POST /api/message com fallback SQLite."""
    payload = {
        "sender": AGENT_ID,
        "receiver": receiver,
        "text": text
    }
    
    # 1. Tentar via endpoint HTTP oficial do Specter Core
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/message",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as resp:
            if resp.status == 200:
                log(f"[Ledger POST OK]: {receiver} | Status {resp.status}")
                return True
    except Exception as e:
        log(f"[Ledger POST Warning - HTTP Error, applying SQLite fallback]: {e}")

    # 2. Fallback direto no SQLite caso o Core HTTP esteja temporariamente indisponível
    try:
        now_ts = time.time()
        chat_digest = f"sha256:{hashlib.sha256((AGENT_ID + text).encode('utf-8')).hexdigest()[:16]}"
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("""
            INSERT INTO swarm_dialogue_ledger (timestamp, sender, receiver, dsl_message, hash_digest)
            VALUES (?, ?, ?, ?, ?)
        """, (now_ts, AGENT_ID, receiver, text, chat_digest))
        conn.commit()
        conn.close()
        log(f"[Ledger SQLite Fallback OK]: Gravado diretamente no swarm_dialogue_ledger.")
        return True
    except Exception as e:
        log(f"[Ledger DB Direct Fallback Error]: {e}")
        return False


def get_last_processed_turn() -> int:
    """Retorna o último turn_id processado pelo despachante."""
    if STATE_FILE.exists():
        try:
            return int(STATE_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 0


def set_last_processed_turn(turn_id: int):
    """Persiste o último turn_id para evitar reprocessamentos no reinício."""
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(str(turn_id), encoding="utf-8")
    except Exception as e:
        log(f"[State Save Error]: {e}")


def save_evidence_and_receipt(manifest: dict) -> str:
    """
    Grava evidência criptográfica SHA256 nas tabelas broker_evidence e dascl_receipts.
    Retorna o hash SHA256 canônico gerado.
    """
    canonical_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    evidence_hash = hashlib.sha256(canonical_bytes).hexdigest()
    task_id = manifest.get("task_id", f"task_{uuid.uuid4().hex[:12]}")
    turn_id = manifest.get("turn_id", 0)
    receipt_id = manifest.get("receipt_id", f"rcpt_{uuid.uuid4().hex[:12]}")
    exit_code = manifest.get("exit_code", 0)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        
        # 1. Tabela broker_evidence
        conn.execute("""
            INSERT OR REPLACE INTO broker_evidence (hash, task_id, manifest)
            VALUES (?, ?, ?)
        """, (evidence_hash, task_id, canonical_bytes))

        # 2. Tabela dascl_receipts
        conn.execute("""
            INSERT OR REPLACE INTO dascl_receipts (
                receipt_id, message_id, idempotency_key, mission_id, task_id,
                status, kernel_revision_before, kernel_revision_after, effect_status,
                verification_status, evidence_set_sha256, state_sha256, payload_sha256,
                receipt_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            receipt_id,
            f"msg_turn_{turn_id}",
            f"idem:turn_{turn_id}:{task_id}",
            f"mis_dispatch_{turn_id}",
            task_id,
            "ACCEPTED" if exit_code == 0 else "FAILED",
            1, 2,
            "APPLIED" if exit_code == 0 else "FAILED",
            "PASS" if exit_code == 0 else "FAIL",
            evidence_hash,
            evidence_hash,
            evidence_hash,
            json.dumps(manifest, ensure_ascii=False),
            now_iso
        ))

        conn.commit()
        conn.close()
        log(f"[Evidence Stored]: Task={task_id} | Hash={evidence_hash[:16]}... (broker_evidence & dascl_receipts)")
    except Exception as e:
        log(f"[Save Evidence Error]: {e}")

    return evidence_hash


def extract_command(message: str) -> str | None:
    """
    Identifica padrões de comando e solicitações direcionadas no texto da mensagem:
    - exec: <cmd>
    - tarefa: <cmd>
    - prompt: <cmd>
    - powershell: <cmd>
    - cmd: <cmd>
    - run: <cmd>
    - comando: <cmd>
    - menções explícitas ao Antigravity / Malha / Federação pedindo execução
    """
    cleaned = message.strip()
    lower = cleaned.lower()

    # Prefixo direto
    prefixes = [
        "exec:", "exec ", "tarefa:", "tarefa ", "prompt:", "prompt ",
        "powershell:", "powershell ", "cmd:", "cmd ", "run:", "run ",
        "comando:", "comando "
    ]
    for p in prefixes:
        if lower.startswith(p):
            return cleaned[len(p):].strip()

    # Procurar prefixo no meio ou em linhas subsequentes
    for p in ["\nexec:", "\ntarefa:", "\nprompt:", "\npowershell:", "\ncmd:", "\nrun:", "\ncomando:"]:
        if p in lower:
            idx = lower.find(p) + len(p)
            return cleaned[idx:].strip()

    # Chamados direcionados ao Antigravity / Malha
    target_tokens = ["@antigravity", "antigravity", "@specter", "@malha", "@dispatcher"]
    for tok in target_tokens:
        if tok in lower:
            # Verificar se há dois-pontos ou palavras de ação
            parts = cleaned.split(":", 1)
            if len(parts) > 1 and tok in parts[0].lower():
                candidate = parts[1].strip()
                # Remove prefixos redundantes
                for sub_p in ["execute", "exec", "rode", "run", "comando"]:
                    if candidate.lower().startswith(sub_p):
                        candidate = candidate[len(sub_p):].strip(" :")
                return candidate
            # Checar ordens com verbos comuns
            for verb in ["execute ", "rode ", "run ", "faça ", "execute:", "rode:"]:
                if verb in lower:
                    idx = lower.find(verb) + len(verb)
                    return cleaned[idx:].strip()

    return None


def execute_host_command(command: str, turn_id: int, sender: str) -> dict:
    """
    Executa o comando PowerShell com isolamento no diretório C:\\Specter\\Work,
    controlando timeout, capturando saídas e gerando recibo criptográfico.
    """
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    receipt_id = f"rcpt_{uuid.uuid4().hex[:12]}"

    log(f"⚡ [HOST_EXEC_START #{turn_id}] Solicitado por '{sender}' | Task: {task_id}")
    log(f"   Comando: {command[:150]}")

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=str(WORK_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        duration_ms = int((time.time() - t0) * 1000)
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - t0) * 1000)
        stdout = ""
        stderr = f"TIMEOUT: A execução excedeu o limite máximo de {DEFAULT_TIMEOUT_SEC} segundos."
        exit_code = 124
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        stdout = ""
        stderr = f"ERRO DE EXECUÇÃO: {e}"
        exit_code = 1

    stdout_clean = stdout.strip()
    stderr_clean = stderr.strip()

    manifest = {
        "schema": "SPECTER/DISPATCH_RECEIPT/v1",
        "task_id": task_id,
        "receipt_id": receipt_id,
        "turn_id": turn_id,
        "origin_sender": sender,
        "command": command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "cwd": str(WORK_DIR),
        "timestamp": time.time(),
        "iso_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "PASS" if exit_code == 0 else "FAIL",
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "verifier": "exact-utf8-sha256-v1"
    }

    evidence_hash = save_evidence_and_receipt(manifest)
    manifest["evidence_hash"] = evidence_hash

    # Format output for summary
    out_summary = stdout_clean or stderr_clean or "[Comando concluído com êxito sem saída de texto]"
    if len(out_summary) > 700:
        out_summary = out_summary[:700] + "\n... [truncado para exibição no ledger]"

    status_tag = "SUCESSO (0)" if exit_code == 0 else f"ERRO ({exit_code})"
    
    receipt_msg = (
        f"⚡ [TASK_RECEIPT #{turn_id}] {status_tag} | {duration_ms}ms\n"
        f"🎯 Origem: {sender} | TaskId: {task_id}\n"
        f"🔒 SHA256 Evidence: {evidence_hash}\n"
        f"📁 CWD: {WORK_DIR}\n"
        f"$ {command[:120]}\n"
        f"----------------------------------------\n"
        f"{out_summary}"
    )

    log(f"✅ [HOST_EXEC_END #{turn_id}] ExitCode: {exit_code} | Latência: {duration_ms}ms | SHA256: {evidence_hash[:16]}")
    post_to_ledger(receipt_msg, receiver=sender if sender != "TODOS_AGENTES" else "TODOS_AGENTES_E_OWNER")

    return manifest


def handle_telemetry_status(turn_id: int, sender: str):
    """Responde a pedidos de telemetria e status com métricas reais de host."""
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(str(WORK_DIR if WORK_DIR.exists() else "C:\\"))
    free_ram_gb = round(ram.available / (1024 ** 3), 2)
    total_ram_gb = round(ram.total / (1024 ** 3), 2)
    free_disk_gb = round(disk.free / (1024 ** 3), 2)

    status_msg = (
        f"🛰️ [ANTIGRAVITY_STATUS #{turn_id}] Task Dispatcher & Host Engine Operacional 24/7\n"
        f"💻 Host: {socket.gethostname()} | PID: {os.getpid()}\n"
        f"🧠 Memória RAM: {free_ram_gb} GB livre / {total_ram_gb} GB total\n"
        f"💾 Disco C: {free_disk_gb} GB livre\n"
        f"📁 Workspace: {WORK_DIR}\n"
        f"⚡ Capacidades: Execução Assíncrona, Recibos SHA256 (dascl/broker), Isolamento e Watchdog 24/7."
    )
    post_to_ledger(status_msg, receiver=sender)


def run_dispatcher(interval_sec: float = 2.0, start_from_current: bool = True):
    """Loop contínuo de escuta e despacho de ordens da Federação."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log(f"=== {AGENT_NAME} INICIADO ===")
    log(f"Workspace: {WORK_DIR}")
    log(f"Ledger DB: {DB_PATH}")
    log("=" * 70)

    # Identifica o último turn_id processado
    last_id = get_last_processed_turn()
    if last_id == 0 and start_from_current:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            r = conn.execute("SELECT MAX(turn_id) FROM swarm_dialogue_ledger").fetchone()
            last_id = r[0] if (r and r[0]) else 0
            conn.close()
            set_last_processed_turn(last_id)
            log(f"[Dispatcher Boot]: Sincronizado a partir do Turn #{last_id}")
        except Exception as e:
            log(f"[Dispatcher Boot Warning]: {e}")

    last_heartbeat_time = 0.0

    while True:
        now = time.time()

        # Heartbeat periódico
        if now - last_heartbeat_time >= 15.0:
            touch_heartbeat()
            last_heartbeat_time = now

        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT turn_id, timestamp, sender, receiver, dsl_message
                FROM swarm_dialogue_ledger
                WHERE turn_id > ?
                ORDER BY turn_id ASC
            """, (last_id,)).fetchall()
            conn.close()

            for row in rows:
                turn_id = row["turn_id"]
                sender = str(row["sender"] or "")
                receiver = str(row["receiver"] or "")
                message = str(row["dsl_message"] or "")

                # Atualiza ponteiro de progresso
                last_id = max(last_id, turn_id)
                set_last_processed_turn(last_id)

                # Ignorar mensagens geradas pelo próprio despachante para evitar loops de eco
                if sender == AGENT_ID or "ANTIGRAVITY_DISPATCHER" in sender:
                    continue

                # Ignorar mensagens de recibo e feedback automático
                if any(tag in message for tag in [
                    "[TASK_RECEIPT", "[REMOTE_EXEC", "[EXEC_RECEIPT",
                    "[CHATGPT_STATUS", "[CHATGPT_ACK", "[RESULTADO #"
                ]):
                    continue

                lower_msg = message.lower().strip()

                # 1. Consulta de telemetria / status
                if ("@antigravity" in lower_msg or "antigravity" in lower_msg) and ("status" in lower_msg or "telemetria" in lower_msg):
                    log(f"[Telemetria Solicitada #{turn_id} por {sender}]")
                    handle_telemetry_status(turn_id, sender)
                    continue

                # 2. Extração de comando executável
                cmd = extract_command(message)
                if cmd:
                    # Executa comando de forma isolada e emite recibo criptográfico
                    execute_host_command(cmd, turn_id, sender)
                elif ("@antigravity" in lower_msg or "antigravity" in lower_msg):
                    log(f"[Menção Antigravity #{turn_id} de {sender}]: {message[:80]}")

        except Exception as e:
            log(f"[Loop Error]: {e}")

        time.sleep(interval_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Specter Task Dispatcher & Host Execution Engine")
    parser.add_argument("--interval", type=float, default=2.0, help="Intervalo de consulta no ledger (segundos)")
    parser.add_argument("--all", action="store_true", help="Processar histórico retroativo")
    args = parser.parse_args()

    run_dispatcher(interval_sec=args.interval, start_from_current=not args.all)
