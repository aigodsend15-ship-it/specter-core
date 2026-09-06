# -*- coding: utf-8 -*-
"""
Specter Operation CLI - Local Operator Control Interface
Comandos:
  python specter_cli.py status           - Exibe PID, uptime, contagem de ciclos e integridade do SQLite WAL
  python specter_cli.py pause            - Coloca o supervisor contínuo em pausa graciosa
  python specter_cli.py resume           - Retoma o ciclo do supervisor contínuo
  python specter_cli.py stop             - Encerra o supervisor contínuo com segurança
  python specter_cli.py submit --text    - Submete novo objetivo/tarefa à fila durável do SQLite
  python specter_cli.py results [ID]     - Consulta tarefas executadas, manifesto e prova SHA-256
  python specter_cli.py nodes            - Lista nós autorizados e orçamentos configurados
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CORE_DIR = Path(r"C:\specter\Core")
HEALTH_FILE = CORE_DIR / "supervisor_health.json"
CONTROL_FILE = CORE_DIR / "supervisor_control.json"
STORAGE_DB = CORE_DIR / "storage" / "specter_fabric.sqlite3"
NODES_CONFIG = CORE_DIR / "config" / "authorized_nodes.json"
PYTHON_EXE = sys.executable

def cmd_status(args):
    if not HEALTH_FILE.exists():
        print("[STATUS] Supervisor não iniciado ou arquivo de saúde não encontrado.")
        return
    data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    pid = data.get("pid")
    is_alive = False
    if pid:
        try:
            out = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True, text=True)
            is_alive = str(pid) in out
        except Exception:
            pass
    print("=" * 60)
    print("SPECTER OPERATIONAL STATUS")
    print("=" * 60)
    print(f"Estado Operacional : {data.get('state', 'UNKNOWN')}")
    status_str = "RODANDO" if is_alive else "PARADO"
    print(f"Saúde Geral        : {data.get('status')} (Processo PID {pid}: {status_str})")
    print(f"Ciclo Atual        : #{data.get('cycle_count')}")
    print(f"Tempo de Uptime    : {data.get('uptime_seconds')}s")
    print(f"Última Atualização : {data.get('formatted_time')}")
    print(f"Banco de Dados     : {data.get('db_path')}")
    db_stats = data.get('db_stats', {})
    print(f"Tamanho DB / WAL   : {db_stats.get('db_size_bytes', 0)} bytes / {db_stats.get('wal_size_bytes', 0)} bytes")
    print("=" * 60)

def cmd_pause(args):
    CONTROL_FILE.write_text(json.dumps({"command": "PAUSE", "timestamp": time.time()}), encoding="utf-8")
    print("[OK] Comando PAUSE gravado. O supervisor entrará em pausa no próximo ciclo.")

def cmd_resume(args):
    CONTROL_FILE.write_text(json.dumps({"command": "RUN", "timestamp": time.time()}), encoding="utf-8")
    print("[OK] Comando RUN gravado. O supervisor retomará o processamento ativo.")

def cmd_stop(args):
    CONTROL_FILE.write_text(json.dumps({"command": "STOP", "timestamp": time.time()}), encoding="utf-8")
    print("[OK] Comando STOP gravado. O supervisor será encerrado graciosamente.")

def cmd_submit(args):
    sys.path.insert(0, str(CORE_DIR))
    import autonomous_broker
    broker = autonomous_broker.Broker(db=STORAGE_DB)
    task_id = broker.submit(text=args.text, idem=args.key, timeout=args.timeout)
    print(f"[OK] Tarefa submetida à fila durável: {task_id}")
    print(f"Texto: {args.text}")

def cmd_results(args):
    import sqlite3
    if not STORAGE_DB.exists():
        print("[ERRO] Banco specter_fabric.sqlite3 não encontrado.")
        return
    with sqlite3.connect(STORAGE_DB) as con:
        con.row_factory = sqlite3.Row
        if args.task_id:
            row = con.execute("SELECT * FROM broker_tasks WHERE id=?", (args.task_id,)).fetchone()
            if not row:
                print(f"[NOT_FOUND] Tarefa {args.task_id} não encontrada.")
                return
            print(f"Tarefa: {row['id']} | Estado: {row['state']} | Versão: {row['version']} | Tentativas: {row['attempts']}")
            print(f"Spec Hash: {row['spec_hash']}")
            print(f"Evidence Hash: {row['evidence_hash']}")
            if row['evidence_hash']:
                ev = con.execute("SELECT manifest FROM broker_evidence WHERE hash=?", (row['evidence_hash'],)).fetchone()
                if ev:
                    manifest_str = ev['manifest'].decode('utf-8', errors='replace')
                    print(f"Manifesto de Evidência:\n{manifest_str}")
        else:
            rows = con.execute("SELECT id, idem, state, spec_hash, evidence_hash FROM broker_tasks ORDER BY rowid DESC LIMIT 10").fetchall()
            print("=" * 75)
            print(f"ÚLTIMAS {len(rows)} TAREFAS NA FILA DURÁVEL")
            print("=" * 75)
            for r in rows:
                ev_str = str(r['evidence_hash'])[:16] if r['evidence_hash'] else "SEM_EVIDÊNCIA"
                print(f"ID: {r['id'][:8]}... | Estado: {r['state']:<10} | Evidence: {ev_str}...")

def cmd_nodes(args):
    if not NODES_CONFIG.exists():
        print("[ERRO] authorized_nodes.json não encontrado.")
        return
    cfg = json.loads(NODES_CONFIG.read_text(encoding="utf-8"))
    print("=" * 75)
    print("NÓS AUTORIZADOS & LIMITES OPERACIONAIS (ZERO CUSTO)")
    print("=" * 75)
    lim = cfg.get("limits", {})
    print(f"Limite Concorrência: {lim.get('max_concurrent_tasks')} | Orçamento Adicional: ${lim.get('max_budget_monthly_usd'):.2f}")
    print(f"Invariante Zero Cobrança: {lim.get('zero_commercial_billing_invariant')}")
    print("-" * 75)
    for n in cfg.get("nodes", []):
        print(f"* {n.get('name'):<22} | Tipo: {n.get('type'):<16} | Status: {n.get('status')}")
        print(f"  Capacidades: {', '.join(n.get('capabilities', []))}")

def main():
    parser = argparse.ArgumentParser(description="Specter Mesh Operator CLI")
    subs = parser.add_subparsers(dest="command", required=True)
    
    subs.add_parser("status", help="Exibe status operacional do supervisor e DB")
    subs.add_parser("pause", help="Pausa a execução do supervisor contínuo")
    subs.add_parser("resume", help="Retoma a execução do supervisor contínuo")
    subs.add_parser("stop", help="Para o supervisor contínuo")
    subs.add_parser("nodes", help="Lista os nós autorizados e orçamentos")
    
    sub = subs.add_parser("submit", help="Submete uma nova tarefa/objetivo")
    sub.add_argument("--text", required=True, help="Texto do objetivo/tarefa")
    sub.add_argument("--key", default=None, help="Chave de idempotência única")
    sub.add_argument("--timeout", type=int, default=120, help="Timeout em segundos")
    
    res = subs.add_parser("results", help="Consulta tarefas e evidências duráveis")
    res.add_argument("task_id", nargs="?", default=None, help="ID opcional da tarefa")
    
    args = parser.parse_args()
    cmds = {
        "status": cmd_status,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "stop": cmd_stop,
        "submit": cmd_submit,
        "results": cmd_results,
        "nodes": cmd_nodes,
    }
    cmds[args.command](args)

if __name__ == "__main__":
    main()
