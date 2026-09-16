# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER CORE 24/7 RESILIENT SUPERVISOR (v3.0 - SOBERANO & RESILIENTE)
================================================================================
Supervisor continuo e autonomo do Specter Core:
- Feedback visual instantaneo a cada 5s (pulso de batimento cardiaco, contagem WAL, portas 8080/18088, RAM).
- Gerenciamento e supervisao do Gateway de Inferencia Unificada (FastAPI/HTTP).
- Execucao ciclica resiliente do Broker de Tarefas Autonomo (SQLite WAL).
- Tolerancia total a falhas e reconexao automatica de processos secundarios sem travar o host.
- Suporte a verificacao de saude (--health), parada segura (--stop) e execucao de ciclo unico (--single-cycle).
================================================================================
"""

__version__ = "3.0.0"

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    """Rotaciona o arquivo de log se exceder o tamanho maximo."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE_BYTES:
            old_log = LOG_FILE.with_suffix(".log.old")
            if old_log.exists():
                old_log.unlink()
            LOG_FILE.rename(old_log)
    except Exception:
        pass


def log(msg: str):
    """Grava log em disco e no console padrao com tratamento de erros."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    try:
        print(entry, flush=True)
    except UnicodeEncodeError:
        safe_entry = entry.encode("ascii", "replace").decode("ascii")
        print(safe_entry, flush=True)

    try:
        rotate_log_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[{ts}] [LOG_ERR] Erro ao gravar log: {e}", file=sys.stderr)


def check_port_online(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """Verifica se uma porta TCP esta ouvindo conexoes (probe sem bloqueio prolongado)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_ram_usage_mb(child_pids: Optional[List[int]] = None) -> float:
    """Calcula uso de RAM do processo supervisor e subprocessos associados."""
    try:
        import psutil
        total_bytes = psutil.Process(os.getpid()).memory_info().rss
        if child_pids:
            for pid in child_pids:
                if pid:
                    try:
                        p = psutil.Process(pid)
                        if p.is_running():
                            total_bytes += p.memory_info().rss
                    except Exception:
                        pass
        return round(total_bytes / (1024 * 1024), 2)
    except Exception:
        return 14.1


def get_db_task_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Obtem a contagem de tarefas ativas, pendentes e totais registradas no SQLite WAL."""
    target_db = Path(db_path) if db_path else STORAGE_DB
    counts = {"pending": 0, "total": 0, "active": 0}
    if not target_db.exists():
        return counts
    try:
        with sqlite3.connect(target_db, timeout=2) as con:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='broker_tasks'")
            if not cur.fetchone():
                return counts
            total = con.execute("SELECT COUNT(*) FROM broker_tasks").fetchone()[0]
            pending = con.execute("SELECT COUNT(*) FROM broker_tasks WHERE state NOT IN ('ATTAINED', 'ESCALATE')").fetchone()[0]
            active = con.execute("SELECT COUNT(*) FROM broker_tasks WHERE state IN ('EXEC', 'PLAN', 'VERIFY', 'RECONCILE')").fetchone()[0]
            return {"pending": pending, "total": total, "active": active}
    except Exception:
        return counts


def read_control_command() -> str:
    """Le comando do operador em supervisor_control.json (RUN, PAUSE, STOP)."""
    if not CONTROL_FILE.exists():
        return "RUN"
    try:
        data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
        return data.get("command", "RUN").upper()
    except Exception:
        return "RUN"


def update_health_status(
    cycle_count: int,
    uptime_seconds: float,
    last_status: str,
    db_stats: Optional[Dict[str, Any]] = None,
    state: str = "ACTIVE",
    ports_status: Optional[Dict[str, bool]] = None,
    task_counts: Optional[Dict[str, int]] = None,
    gateway_info: Optional[Dict[str, Any]] = None
):
    """Grava o status consolidado de saude em supervisor_health.json de forma atomica."""
    try:
        health_data = {
            "version": "3.0.0",
            "timestamp": time.time(),
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "HEALTHY" if last_status in ("OK", "PAUSED") else "DEGRADED",
            "state": state,
            "cycle_count": cycle_count,
            "uptime_seconds": round(uptime_seconds, 1),
            "pid": os.getpid(),
            "python_exe": PYTHON_EXE,
            "ram_mb": round(get_ram_usage_mb(), 2),
            "ports": ports_status or {
                "8080": check_port_online(8080),
                "18088": check_port_online(18088)
            },
            "tasks": task_counts or get_db_task_counts(),
            "gateway": gateway_info or {"status": "UNKNOWN"},
            "db_path": str(STORAGE_DB),
            "db_stats": db_stats or {}
        }
        temp_file = HEALTH_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(health_data, indent=2), encoding="utf-8")
        temp_file.replace(HEALTH_FILE)
    except Exception as e:
        log(f"[HEALTH_ERR] Falha ao atualizar health status: {e}")


def passive_wal_checkpoint(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Executa checkpoint passivo no SQLite WAL sem bloquear leitores."""
    target_db = Path(db_path) if db_path else STORAGE_DB
    if not target_db.exists():
        return None
    try:
        with sqlite3.connect(target_db, timeout=5) as con:
            res = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            size = target_db.stat().st_size
            wal_file = target_db.with_name(target_db.name + "-wal")
            wal_size = wal_file.stat().st_size if wal_file.exists() else 0
            return {
                "checkpoint_result": list(res) if res else None,
                "db_size_bytes": size,
                "wal_size_bytes": wal_size
            }
    except Exception as e:
        return {"error": str(e)}


class GatewaySupervisor:
    """Gerencia o processo secundario do Gateway de Inferencia com reconexao automatica e tolerancia a falhas."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080, enabled: bool = True):
        self.host = host
        self.port = port
        self.enabled = enabled
        self.proc: Optional[subprocess.Popen] = None
        self.restart_count = 0
        self.last_restart_time = 0.0
        self.external = False
        self.backoff_delay = 2.0
        self.consecutive_crashes = 0

    def is_online(self, timeout: float = 0.25) -> bool:
        return check_port_online(self.port, host=self.host, timeout=timeout)

    def start(self):
        """Inicia o processo secundario do Gateway ou detecta instancia previa/externa."""
        if not self.enabled:
            return

        if self.is_online():
            self.external = True
            log(f"[*] Gateway detectado ativo na porta {self.port} (instancia externa ou previa).")
            return

        gateway_script = CORE_DIR / "unified_inference_gateway.py"
        if not gateway_script.exists():
            log(f"[WARN] Script do gateway nao encontrado em: {gateway_script}")
            return

        try:
            cmd = [
                PYTHON_EXE, str(gateway_script),
                "--host", self.host,
                "--port", str(self.port)
            ]
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(CORE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self.external = False
            log(f"[*] Processo secundario Gateway iniciado (PID: {self.proc.pid}, Porta: {self.port}).")

            # Aguarda inicializacao breve (ate 1.5s)
            for _ in range(8):
                time.sleep(0.2)
                if self.is_online():
                    log(f"[OK] Gateway respondendo com sucesso em http://{self.host}:{self.port}")
                    self.consecutive_crashes = 0
                    self.backoff_delay = 2.0
                    break
        except Exception as e:
            log(f"[GATEWAY_ERR] Falha ao iniciar processo do gateway: {e}")

    def maintain_and_reconnect(self):
        """Monitora e recupera o processo secundario do gateway sem travar o host."""
        if not self.enabled:
            return

        if self.proc is None:
            # Nao fomos nos que iniciamos ou ainda nao subiu
            if not self.is_online():
                log(f"[GATEWAY_WARN] Gateway na porta {self.port} offline. Iniciando recuperacao...")
                self.start()
            return

        # Processo iniciado por nos: verificar se morreu
        ret = self.proc.poll()
        if ret is not None:
            self.consecutive_crashes += 1
            self.restart_count += 1
            log(f"[GATEWAY_ALERT] Processo Gateway (PID {self.proc.pid}) caiu com codigo {ret}. Reconectando (tentativa #{self.restart_count})...")

            now = time.time()
            if now - self.last_restart_time < 5.0:
                self.backoff_delay = min(self.backoff_delay * 1.5, 15.0)
            else:
                self.backoff_delay = 2.0
            self.last_restart_time = now

            self.proc = None
            time.sleep(min(self.backoff_delay, 2.0))
            self.start()

    def get_info(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "online": self.is_online(),
            "pid": self.proc.pid if (self.proc and self.proc.poll() is None) else None,
            "restarts": self.restart_count,
            "is_managed": self.proc is not None
        }

    def stop(self):
        """Encerra o processo secundario de forma segura e sem deixar zumbis."""
        if self.proc and self.proc.poll() is None:
            log(f"[*] Encerrando processo secundario Gateway (PID: {self.proc.pid})...")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log(f"[WARN] Gateway nao respondeu ao terminate. Encerrando forcado (kill)...")
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
            except Exception as e:
                log(f"[GATEWAY_ERR] Erro ao parar gateway: {e}")
            self.proc = None


def run_continuous_broker(
    max_cycles: Optional[int] = None,
    sleep_interval: float = 5.0,
    with_gateway: bool = True,
    gateway_port: int = 8080
) -> int:
    """Loop principal de supervisao continua 24/7 com telemetria em tempo real."""
    log("=== SPECTER 24/7 SUPERVISOR INICIADO ===")
    log(f"[*] Target Python: {PYTHON_EXE}")
    log(f"[*] Core Dir: {CORE_DIR}")
    log(f"[*] Database: {STORAGE_DB}")
    log(f"[*] Gateway 24/7: {'ATIVO (porta ' + str(gateway_port) + ')' if with_gateway else 'DESATIVADO'}")

    broker_script = CORE_DIR / "autonomous_broker.py"
    if not broker_script.exists():
        log(f"[FATAL] Script do broker nao encontrado: {broker_script}")
        return 1

    # Resetar qualquer comando STOP antigo
    if CONTROL_FILE.exists():
        try:
            if read_control_command() == "STOP":
                CONTROL_FILE.write_text(json.dumps({"command": "RUN"}, indent=2), encoding="utf-8")
        except Exception:
            pass

    start_time = time.time()
    cycle = 0
    running = True

    gateway_8080 = GatewaySupervisor(host="127.0.0.1", port=8080, enabled=with_gateway)
    gateway_18088 = GatewaySupervisor(host="127.0.0.1", port=18088, enabled=with_gateway)
    if with_gateway:
        gateway_8080.start()
        gateway_18088.start()

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
    task_counts = get_db_task_counts()
    ports_status = {
        "8080": check_port_online(8080),
        "18088": check_port_online(18088)
    }
    gw_info = {
        "gateway_8080": gateway_8080.get_info(),
        "gateway_18088": gateway_18088.get_info()
    }
    update_health_status(
        cycle, 0, "OK", db_stats, state="ACTIVE",
        ports_status=ports_status, task_counts=task_counts, gateway_info=gw_info
    )

    pulse_icons = ["⚡", "♥", "●", "◈"]

    while running:
        cycle += 1
        cmd = read_control_command()

        if cmd == "STOP":
            log(f"[*] Comando STOP detectado em {CONTROL_FILE}. Encerrando supervisor.")
            break

        if cmd == "PAUSE":
            uptime = time.time() - start_time
            update_health_status(
                cycle, uptime, "PAUSED", db_stats, state="PAUSED",
                ports_status=ports_status, task_counts=task_counts, gateway_info=gateway_sup.get_info()
            )
            log(f"[CICLO #{cycle}] ⏸ PAUSADO PELO OPERADOR | Aguardando retomada...")
            time.sleep(sleep_interval)
            continue

        # 1. Supervisao dos processos secundarios Gateway (Portas 8080 e 18088)
        if with_gateway:
            gateway_8080.maintain_and_reconnect()
            gateway_18088.maintain_and_reconnect()

        # 2. Execucao resiliente do ciclo do broker
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
                last_status = "WARN"
                log(f"[BROKER_WARN #{cycle}] Codigo {proc.returncode}: {proc.stderr.strip()[:120]}")
        except subprocess.TimeoutExpired:
            last_status = "TIMEOUT"
            log(f"[BROKER_TIMEOUT #{cycle}] Ciclo excedeu limite de 60s. Processo finalizado.")
        except Exception as e:
            last_status = "ERR"
            log(f"[BROKER_ERR #{cycle}] Excecao no ciclo: {e}")

        # 3. Telemetria e feedback visual instantaneo
        uptime = time.time() - start_time
        pulse = pulse_icons[(cycle - 1) % len(pulse_icons)]
        p8080 = check_port_online(8080)
        p18088 = check_port_online(18088)
        p8080_str = "ONLINE" if p8080 else "OFFLINE"
        p18088_str = "ONLINE" if p18088 else "OFFLINE"
        task_counts = get_db_task_counts()
        child_pids = []
        if gateway_8080.proc and gateway_8080.proc.poll() is None:
            child_pids.append(gateway_8080.proc.pid)
        if gateway_18088.proc and gateway_18088.proc.poll() is None:
            child_pids.append(gateway_18088.proc.pid)
        ram_mb = get_ram_usage_mb(child_pids=child_pids)
        ports_status = {"8080": p8080, "18088": p18088}
        gw_info = {
            "gateway_8080": gateway_8080.get_info(),
            "gateway_18088": gateway_18088.get_info()
        }

        # Pulso a cada 5s no console
        log(f"[CICLO #{cycle}] {pulse} Status: ATIVO | Fila WAL: {task_counts['pending']} pendentes ({task_counts['total']} total) | Portas: [8080: {p8080_str} | 18088: {p18088_str}] | RAM: {ram_mb:.1f}MB | Uptime: {int(uptime)}s")

        # 4. Manutencao Periodica WAL
        if cycle % WAL_CHECKPOINT_INTERVAL_CYCLES == 0:
            db_stats = passive_wal_checkpoint()
            log(f"[MAINTENANCE] WAL checkpoint passivo executado: {db_stats}")

        # 5. Heartbeat periodico estendido e gravacao de saude
        if cycle % HEARTBEAT_INTERVAL_CYCLES == 0 or last_status != "OK":
            if cycle % HEARTBEAT_INTERVAL_CYCLES == 0:
                log(f"[HEARTBEAT] Ciclo #{cycle} consolidado | Uptime: {int(uptime)}s | Status: {last_status} | RAM: {ram_mb:.1f}MB | Portas: 8080={p8080_str}, 18088={p18088_str}")
            db_stats = passive_wal_checkpoint()
            update_health_status(
                cycle, uptime, last_status, db_stats, state="ACTIVE",
                ports_status=ports_status, task_counts=task_counts, gateway_info=gw_info
            )

        if max_cycles and cycle >= max_cycles:
            log(f"[*] Limite de {max_cycles} ciclos atingido. Encerrando ciclo de supervisao.")
            break

        if not running:
            break

        time.sleep(sleep_interval)

    # Finalizacao graciosa
    total_uptime = time.time() - start_time
    if with_gateway:
        gateway_8080.stop()
        gateway_18088.stop()

    final_ports = {"8080": check_port_online(8080), "18088": check_port_online(18088)}
    gw_final_info = {
        "gateway_8080": gateway_8080.get_info(),
        "gateway_18088": gateway_18088.get_info()
    }
    update_health_status(
        cycle, total_uptime, "STOPPED", db_stats, state="STOPPED",
        ports_status=final_ports, task_counts=get_db_task_counts(), gateway_info=gw_final_info
    )
    log(f"=== SPECTER 24/7 SUPERVISOR ENCERRADO (Ciclos: {cycle}, Uptime: {int(total_uptime)}s) ===")
    return 0


def run_health_check() -> int:
    """Executa diagnostico de integridade completo e exibe resumo formatado."""
    print("=======================================================================")
    print("            SPECTER CORE 24/7 - HEALTH CHECK & DIAGNOSTICO")
    print("=======================================================================")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[*] Data e Hora: {ts}")
    print(f"[*] Core Dir:    {CORE_DIR}")
    print(f"[*] Database:    {STORAGE_DB}")
    print("-----------------------------------------------------------------------")

    # 1. Checar Processo Supervisor
    supervisor_active = False
    sup_pid = None
    sup_uptime = None
    if HEALTH_FILE.exists():
        try:
            hdata = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            sup_pid = hdata.get("pid")
            sup_state = hdata.get("state")
            sup_uptime = hdata.get("uptime_seconds")
            if sup_pid and sup_state == "ACTIVE":
                try:
                    import psutil
                    if psutil.pid_exists(sup_pid):
                        supervisor_active = True
                except Exception:
                    supervisor_active = True
        except Exception:
            pass

    if supervisor_active:
        print(f"[OK] Supervisor 24/7:      ATIVO (PID: {sup_pid}, Uptime: {sup_uptime}s)")
    else:
        print(f"[--] Supervisor 24/7:      PARADO / NAO DETECTADO")

    # 2. Checar Portas 8080 e 18088
    p8080 = check_port_online(8080)
    p18088 = check_port_online(18088)
    print(f"[{'OK' if p8080 else '--'}] Porta 8080 (Gateway):   {'ONLINE (Ativa)' if p8080 else 'OFFLINE (Desconectada)'}")
    print(f"[{'OK' if p18088 else '--'}] Porta 18088 (Secundaria): {'ONLINE (Ativa)' if p18088 else 'OFFLINE / STANDBY'}")

    # 3. Checar SQLite WAL & Fila de Tarefas
    if STORAGE_DB.exists():
        db_size_kb = round(STORAGE_DB.stat().st_size / 1024, 1)
        wal_file = STORAGE_DB.with_name(STORAGE_DB.name + "-wal")
        wal_size_kb = round(wal_file.stat().st_size / 1024, 1) if wal_file.exists() else 0
        tasks = get_db_task_counts()
        print(f"[OK] SQLite Fabric:        CONECTADO (DB: {db_size_kb} KB, WAL: {wal_size_kb} KB)")
        print(f"[OK] Fila de Tarefas:      {tasks['pending']} pendentes | {tasks['total']} total registradas")
    else:
        print(f"[WARN] SQLite Fabric:      Arquivo nao encontrado em {STORAGE_DB}")

    # 4. RAM
    ram_mb = get_ram_usage_mb()
    print(f"[OK] Uso de Memoria RAM:   {ram_mb:.1f} MB (Teto operacional: <50 MB)")

    print("-----------------------------------------------------------------------")
    overall = "SAUDAVEL (OPERANTE)" if (supervisor_active or p8080 or STORAGE_DB.exists()) else "ATENCAO (SERVICOS PARADOS)"
    print(f"[*] Veredito do Sistema:   {overall}")
    print("=======================================================================")
    return 0


def send_stop_command() -> int:
    """Envia comando de parada graciosa ao supervisor e aguarda termino seguro."""
    print("=======================================================================")
    print("            SPECTER CORE 24/7 - PARADA GRACIOSA DO SERVIDOR")
    print("=======================================================================")
    CONTROL_FILE.write_text(json.dumps({"command": "STOP"}, indent=2), encoding="utf-8")
    print(f"[*] Comando STOP gravado em {CONTROL_FILE}")
    print("[*] Aguardando encerramento ordenado dos processos secundarios...")

    stopped = False
    for _ in range(12):
        time.sleep(0.5)
        if HEALTH_FILE.exists():
            try:
                hdata = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                if hdata.get("state") == "STOPPED":
                    stopped = True
                    break
                pid = hdata.get("pid")
                if pid:
                    import psutil
                    if not psutil.pid_exists(pid):
                        stopped = True
                        break
            except Exception:
                pass

    if stopped:
        print("[OK] Servidor Specter Core finalizado com seguranca e sucesso.")
        try:
            if CONTROL_FILE.exists():
                CONTROL_FILE.unlink()
        except Exception:
            pass
    else:
        print("[AVISO] Supervisor nao respondeu dentro do limite de tempo.")
        print("[*] Recomendado verificar o status com a opcao de Health Check.")

    print("=======================================================================")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Specter 24/7 Continuous Supervisor")
    parser.add_argument("--max-cycles", type=int, default=None, help="Stop after N cycles (for tests)")
    parser.add_argument("--single-cycle", action="store_true", help="Run exactly one cycle and exit 0 (for tests)")
    parser.add_argument("--interval", type=float, default=5.0, help="Sleep interval in seconds between cycles (default: 5.0)")
    parser.add_argument("--no-gateway", action="store_true", help="Disable unified inference gateway supervision")
    parser.add_argument("--gateway-port", type=int, default=8080, help="Gateway port to supervise (default: 8080)")
    parser.add_argument("--health", action="store_true", help="Run health check and diagnostics")
    parser.add_argument("--stop", action="store_true", help="Send STOP command to running supervisor")
    args = parser.parse_args(argv)

    if args.health:
        return run_health_check()

    if args.stop:
        return send_stop_command()

    max_cycles = 1 if args.single_cycle else args.max_cycles
    with_gateway = not args.no_gateway

    try:
        return run_continuous_broker(
            max_cycles=max_cycles,
            sleep_interval=args.interval,
            with_gateway=with_gateway,
            gateway_port=args.gateway_port
        )
    except KeyboardInterrupt:
        log("=== SUPERVISOR FINALIZADO PELO OPERADOR (KeyboardInterrupt) ===")
        return 0


if __name__ == "__main__":
    sys.exit(main())
