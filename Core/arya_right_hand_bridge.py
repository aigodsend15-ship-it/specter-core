# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER CORE // ARYA FAIRY RIGHT-HAND BRIDGE (arya_right_hand_bridge.py)
Autonomous Desktop Activity Observer and Tri-Agent Co-Pilot
Principle: OBSERVE -> REMEMBER -> PLAN -> PROPOSE
Zero Commercial Tokens ($0.00 USD)
================================================================================
"""

import os
import sys
import time
import json
import sqlite3
import ctypes
import ctypes.wintypes
from pathlib import Path
from typing import Dict, Any, Optional, List

SPECTER_ROOT = Path(os.getenv("SPECTER_ROOT", r"C:\specter\Core"))
ARYA_ROOT = Path(os.getenv("ARYA_ROOT", Path.home() / "Documents" / "Arya"))
DB_PATH = SPECTER_ROOT / "storage" / "specter_fabric.sqlite3"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def get_foreground_window_info() -> Dict[str, Any]:
    """Extract active window title and process ID using Win32 API without intrusive hooks."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "Desktop / Idle", "pid": 0, "timestamp": time.time()}
    
    length = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    title = buff.value
    
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    
    return {
        "hwnd": hwnd,
        "title": title,
        "pid": pid.value,
        "timestamp": time.time()
    }

class AryaRightHandBridge:
    def __init__(self, db_path: Optional[Path] = None, arya_root: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.arya_root = Path(arya_root) if arya_root else ARYA_ROOT
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS arya_desktop_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                window_title TEXT,
                process_id INTEGER,
                category TEXT,
                action_context TEXT,
                proactive_suggestion TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_arya_obs_ts ON arya_desktop_observations(timestamp);")

    def record_observation(self, info: Dict[str, Any], category: str = "development") -> int:
        with self._get_conn() as conn:
            cur = conn.execute("""
            INSERT INTO arya_desktop_observations (timestamp, window_title, process_id, category, action_context)
            VALUES (?, ?, ?, ?, ?)
            """, (info["timestamp"], info["title"], info["pid"], category, json.dumps(info)))
            return cur.lastrowid

    def get_recent_observations(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM arya_desktop_observations ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def sync_to_arya_journal(self, summary: str):
        journal_dir = self.arya_root / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        journal_file = journal_dir / f"{today}.md"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(f"\n\n### [{time.strftime('%H:%M:%S')}] Observação Right-Hand\n{summary}\n")

if __name__ == "__main__":
    print("=== ARYA FAIRY RIGHT-HAND BRIDGE ===")
    bridge = AryaRightHandBridge()
    info = get_foreground_window_info()
    obs_id = bridge.record_observation(info)
    print(f"[+] Observação #{obs_id} gravada: {info['title']} (PID: {info['pid']})")
    bridge.sync_to_arya_journal(f"Janela ativa observada: {info['title']}")
    print(f"[+] Sincronizado com o diário da Arya em C:\\Users\\USER\\Documents\\Arya\\journal")
