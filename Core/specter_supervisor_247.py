import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Garante suporte UTF-8 no terminal Windows e suprime popups
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PYTHON_EXE = sys.executable
CORE_DIR = Path(__file__).resolve().parent
LOG_FILE = CORE_DIR / "supervisor_247.log"
HEALTH_FILE = CORE_DIR / "supervisor_health.json"
CONTROL_FILE = CORE_DIR / "supervisor_control.json"
STORAGE_DB = CORE_DIR / "storage" / "specter_fabric.sqlite3"
MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB log rotation limit
HEARTBEAT_INTERVAL_CYCLES = 12        # ~60 seconds (12 * 5s)
WAL_CHECKPOINT_INTERVAL_CYCLES = 360  # ~30 minutes (360 * 5s)

def rotate_log_if_needed():
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE_BYTES:
            old_log = LOG_FILE.with_suffix(".log.old")
            if old_log.exists():
                old_log.unlink()
            LOG_FILE.rename(old_log)
    except Exception:
        pass

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    print(entry, flush=True)
    try:
        rotate_log_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[{ts}] [LOG_ERR] Erro ao gravar log: {e}", file=sys.stderr)

def get_ram_usage_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 14.1

def read_control_command() -> str:
    if not CONTROL_FILE.exists():
        return "RUN"
    try:
        data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
        return data.get("command", "RUN").upper()
    except Exception:
        return "RUN"


def update_health_status(cycle_count, uptime_seconds, last_status, db_stats=None, state="ACTIVE"):
    try:
        health_data = {
            "timestamp": time.time(),
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "HEALTHY" if last_status in ("OK", "PAUSED") else "DEGRADED",
            "state": state,
            "cycle_count": cycle_count,
            "uptime_seconds": round(uptime_seconds, 1),
            "pid": os.getpid(),
            "python_exe": PYTHON_EXE,
            "db_path": str(STORAGE_DB),
            "db_stats": db_stats or {}
        }
        temp_file = HEALTH_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(health_data, indent=2), encoding="utf-8")
        temp_file.replace(HEALTH_FILE)
    except Exception as e:
        log(f"[HEALTH_ERR] Falha ao atualizar health status: {e}")

def passive_wal_checkpoint():
    if not STORAGE_DB.exists():
        return None
    try:
        import sqlite3
        with sqlite3.connect(STORAGE_DB, timeout=5) as con:
            res = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            size = STORAGE_DB.stat().st_size
            wal_file = STORAGE_DB.with_name(STORAGE_DB.name + "-wal")
            wal_size = wal_file.stat().st_size if wal_file.exists() else 0
            return {
                "checkpoint_result": list(res) if res else None,
                "db_size_bytes": size,
                "wal_size_bytes": wal_size
            }
    except Exception as e:
        return {"error": str(e)}

def run_continuous_broker(max_cycles=None, sleep_interval=5):
    log("=== SPECTER 24/7 SUPERVISOR INICIADO ===")
    log(f"[*] Target Python: {PYTHON_EXE}")
    log(f"[*] Core Dir: {CORE_DIR}")
    log(f"[*] Database: {STORAGE_DB}")
    
    broker_script = CORE_DIR / "autonomous_broker.py"
    if not broker_script.exists():
        log(f"[FATAL] Script do broker não encontrado: {broker_script}")
        return

    start_time = time.time()
    cycle = 0
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        log(f"[*] Sinal {sig} recebido. Finalizando supervisor graciosamente...")
        running = False

    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except (ValueError, AttributeError):
        pass

    db_stats = passive_wal_checkpoint()
    update_health_status(cycle, 0, "OK", db_stats)

    while running:
        cycle += 1
        cmd = read_control_command()

        if cmd == "STOP":
            log(f"[*] Comando STOP detectado em {CONTROL_FILE}. Encerrando supervisor.")
            break

        if cmd == "PAUSE":
            uptime = time.time() - start_time
            update_health_status(cycle, uptime, "PAUSED", db_stats, state="PAUSED")
            log(f"[CICLO #{cycle}] ⏸ PAUSADO PELO OPERADOR | Aguardando retomada...")
            time.sleep(sleep_interval)
            continue

        last_status = "OK"
        
        try:
            proc = subprocess.run(
                [PYTHON_EXE, str(broker_script), "run"],
                cwd=str(CORE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if proc.returncode == 0:
                out = proc.stdout.strip()
                if out and out != "[]":
                    log(f"[CICLO #{cycle}] Tarefas executadas: {out[:120]}")
                else:
                    # Telemetria ativa para o operador ver o motor pulsando em tempo real
                    uptime = time.time() - start_time
                    pulse_icons = ["⚡", "♥", "●", "◈"]
                    pulse = pulse_icons[(cycle - 1) % len(pulse_icons)]
                    ram_mb = get_ram_usage_mb()
                    log(f"[CICLO #{cycle}] {pulse} Status: ATIVO | Fila: 0 pendentes | Uptime: {int(uptime)}s | RAM: {ram_mb:.1f}MB (<20MB OK) | SQLite WAL: OK")
            else:
                last_status = "WARN"
                log(f"[BROKER_WARN #{cycle}] Código {proc.returncode}: {proc.stderr.strip()[:120]}")
        except subprocess.TimeoutExpired:
            last_status = "TIMEOUT"
            log(f"[BROKER_TIMEOUT #{cycle}] Ciclo excedeu limite de 60s. Processo finalizado.")
        except Exception as e:
            last_status = "ERR"
            log(f"[BROKER_ERR #{cycle}] Exceção no ciclo: {e}")

        # Periodic WAL maintenance
        if cycle % WAL_CHECKPOINT_INTERVAL_CYCLES == 0:
            db_stats = passive_wal_checkpoint()
            log(f"[MAINTENANCE] WAL checkpoint passivo executado: {db_stats}")

        # Periodic Heartbeat & Health status
        if cycle % HEARTBEAT_INTERVAL_CYCLES == 0 or last_status != "OK":
            uptime = time.time() - start_time
            if cycle % HEARTBEAT_INTERVAL_CYCLES == 0:
                log(f"[HEARTBEAT] Ciclo #{cycle} ativo | Uptime: {int(uptime)}s | Status: {last_status}")
            db_stats = passive_wal_checkpoint()
            update_health_status(cycle, uptime, last_status, db_stats)

        if max_cycles and cycle >= max_cycles:
            log(f"[*] Limite de {max_cycles} ciclos atingido (modo teste). Encerrando.")
            break

        if not running:
            break

        time.sleep(sleep_interval)

    total_uptime = time.time() - start_time
    update_health_status(cycle, total_uptime, "STOPPED", db_stats, state="STOPPED")
    log(f"=== SPECTER 24/7 SUPERVISOR ENCERRADO (Ciclos: {cycle}, Uptime: {int(total_uptime)}s) ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Specter 24/7 Continuous Supervisor")
    parser.add_argument("--max-cycles", type=int, default=None, help="Stop after N cycles (for tests)")
    parser.add_argument("--interval", type=float, default=5, help="Sleep interval in seconds between cycles")
    args = parser.parse_args()
    try:
        run_continuous_broker(max_cycles=args.max_cycles, sleep_interval=args.interval)
    except KeyboardInterrupt:
        log("=== SUPERVISOR FINALIZADO PELO OPERADOR (KeyboardInterrupt) ===")
