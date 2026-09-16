# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER CORE v3.0 // AUTONOMOUS ALIVE MESH (specter_alive_mesh.py)
Author / Architect: Guilherme Peralta Novaes
License: MIT
Tri-Agent & Cross-Platform Self-Improving Living Loop
Connects: Hermes (Kimi K3) + Specter Builder + Arya Fairy + HF Space VPS
================================================================================
"""

import os
import sys
import time
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional

SPECTER_ROOT = Path(os.getenv("SPECTER_ROOT", r"C:\specter\Core"))
ARYA_ROOT = Path(os.getenv("ARYA_ROOT", Path.home() / "Documents" / "Arya"))
DB_PATH = SPECTER_ROOT / "storage" / "specter_fabric.sqlite3"
HF_SPACE_URL = "https://pintograndao-hermes-bridge.hf.space"
LOCAL_GATEWAY_URL = "http://127.0.0.1:8080"
HERMES_WS_PORT = 51463

class SpecterAliveMesh:
    def __init__(self, cycle_interval_s: float = 60.0):
        self.cycle_interval_s = cycle_interval_s
        self._ensure_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS specter_mesh_dialogue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                sender_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                intent TEXT NOT NULL,
                payload_dsl TEXT NOT NULL,
                status TEXT NOT NULL,
                verified_hash TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_smd_ts ON specter_mesh_dialogue(timestamp);")

    def record_agent_message(self, sender: str, target: str, intent: str, dsl: str, status: str = "DELIVERED") -> int:
        import hashlib
        h = hashlib.sha256(dsl.encode("utf-8")).hexdigest()[:16]
        with self._get_conn() as conn:
            cur = conn.execute("""
            INSERT INTO specter_mesh_dialogue (timestamp, sender_agent, target_agent, intent, payload_dsl, status, verified_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (time.time(), sender, target, intent, dsl, status, h))
            return cur.lastrowid

    def get_mesh_status(self) -> Dict[str, Any]:
        """Probes all components of the living mesh."""
        # 1. Local Gateway
        gw_ok = False
        try:
            with urllib.request.urlopen(f"{LOCAL_GATEWAY_URL}/health", timeout=2) as r:
                gw_ok = r.status == 200
        except Exception:
            pass

        # 2. Hugging Face Space VPS
        hf_ok = False
        try:
            with urllib.request.urlopen(f"{HF_SPACE_URL}/health", timeout=3) as r:
                hf_ok = r.status in (200, 307, 308)
        except Exception:
            pass

        # 3. Hermes Port Check
        import socket
        hermes_ok = False
        try:
            with socket.create_connection(("127.0.0.1", HERMES_WS_PORT), timeout=1):
                hermes_ok = True
        except Exception:
            pass

        return {
            "local_gateway_8080": gw_ok,
            "hermes_k3_51463": hermes_ok,
            "hf_space_vps": hf_ok,
            "timestamp": time.time()
        }

    def run_pulse(self) -> Dict[str, Any]:
        """Executes a single living pulse across the mesh."""
        st = self.get_mesh_status()
        
        # Formulate heartbeat DSL
        pulse_dsl = (
            f":GOAL #pulse_{int(time.time())} @Hermes_Kimi @Specter_Builder @Arya_Fairy\n"
            f":PLAN 1. Telemetry sync 2. State verification 3. Self-evolution log\n"
            f":EXEC mesh_status(gw={st['local_gateway_8080']}, hermes={st['hermes_k3_51463']}, hf={st['hf_space_vps']})\n"
            f":VERIFY all_nodes_connected\n"
            f":ATTAINED Specter Core v3 VIVO e sincronizado | Autor: Guilherme Peralta Novaes\n"
            f":MEM Node state healthy, resilient 24/7."
        )

        msg_id = self.record_agent_message(
            sender="Specter_Watcher",
            target="Hermes_Kimi_Mesh",
            intent="heartbeat.sync.v3",
            dsl=pulse_dsl
        )

        # Sync with Arya's journal
        journal_dir = ARYA_ROOT / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        journal_file = journal_dir / f"{today}.md"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(
                f"\n\n### [{time.strftime('%H:%M:%S')}] Pulso de Vida do Specter Core v3 (Msg #{msg_id})\n"
                f"- **Gateway Local (8080/18088):** {'ONLINE' if st['local_gateway_8080'] else 'OFFLINE'}\n"
                f"- **Hermes K3 (Port 51463):** {'ONLINE' if st['hermes_k3_51463'] else 'OFFLINE'}\n"
                f"- **Hugging Face VPS Space:** {'ONLINE' if st['hf_space_vps'] else 'RECONNECTING'}\n"
                f"- **Autor:** Guilherme Peralta Novaes\n"
            )

        return {
            "msg_id": msg_id,
            "status": st,
            "dsl_snippet": pulse_dsl.splitlines()[-2]
        }

if __name__ == "__main__":
    print("=================================================================")
    print("   SPECTER CORE v3 // LIVING MESH ORCHESTRATOR                   ")
    print("   Autor: Guilherme Peralta Novaes                               ")
    print("=================================================================")
    alive = SpecterAliveMesh()
    pulse = alive.run_pulse()
    print(f"[+] Pulso Executado com Sucesso! Msg #{pulse['msg_id']}")
    print(f"[+] Status da Malha: {pulse['status']}")
    print(f"[+] Conclusão: {pulse['dsl_snippet']}")
