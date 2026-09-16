# -*- coding: utf-8 -*-
"""Deterministic runtime evidence collector for SPECTER Core v3.

Standard-library only. It never reads or emits credentials. The report separates
reachability, SQLite durability, local resource observations, and artifact hashes.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def http_probe(url: str, timeout: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": "SpecterRuntimeAudit/3"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(512)
            return {
                "ok": 200 <= int(r.status) < 400,
                "status_code": int(r.status),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "content_type": r.headers.get("Content-Type", ""),
                "body_preview": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status_code": int(exc.code),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"HTTPError: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def memory_bytes() -> int | None:
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)
        return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return None


def sqlite_evidence(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "path": str(db_path)}
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            peers = []
            if "federation_peers" in tables:
                conn.row_factory = sqlite3.Row
                peers = [dict(r) for r in conn.execute(
                    "SELECT peer_id,status,last_seen,checked_at,last_error "
                    "FROM federation_peers ORDER BY peer_id"
                )]
            return {
                "exists": True,
                "path": str(db_path),
                "journal_mode": mode,
                "wal_verified": mode == "wal",
                "tables": tables,
                "federation_peers": peers,
            }
    except Exception as exc:
        return {"exists": True, "path": str(db_path), "error": f"{type(exc).__name__}: {exc}"}


def build_report(root: Path) -> dict[str, Any]:
    hf = os.getenv("SPECTER_HF_SPACE_URL", "https://pintograndao-hermes-bridge.hf.space").rstrip("/")
    local = os.getenv("SPECTER_LOCAL_URL", "http://127.0.0.1:8080").rstrip("/")
    hermes_host = os.getenv("SPECTER_HERMES_HOST", "127.0.0.1")
    hermes_port = int(os.getenv("SPECTER_HERMES_PORT", "51463"))
    db_path = Path(os.getenv("SPECTER_FABRIC_DB", str(root / "storage" / "specter_fabric.sqlite3")))

    artifact_names = [
        "specter_federation_mesh.py",
        "specter_alive_mesh.py",
        "auto_healer.py",
        "unified_inference_gateway.py",
        "specter_sol_protocol.py",
        "omni_mesh_fabric.py",
    ]
    artifacts: dict[str, Any] = {}
    for name in artifact_names:
        p = root / name
        artifacts[name] = (
            {"exists": True, "size_bytes": p.stat().st_size, "sha256": sha256_file(p)}
            if p.exists() else {"exists": False}
        )

    return {
        "schema": "specter-runtime-audit/1",
        "generated_unix": time.time(),
        "root": str(root),
        "local_resources": {
            "logical_cpu_count": os.cpu_count(),
            "physical_memory_bytes": memory_bytes(),
            "platform": os.name,
        },
        "probes": {
            "local_8080_health": http_probe(f"{local}/health"),
            "local_18088_health": http_probe(
                f"{os.getenv('SPECTER_GATEWAY_18088', 'http://127.0.0.1:18088').rstrip('/')}/health"
            ),
            "hermes_tcp": tcp_probe(hermes_host, hermes_port),
            "hf_space_root": http_probe(f"{hf}/"),
            "hf_space_health": http_probe(f"{hf}/health"),
        },
        "sqlite": sqlite_evidence(db_path),
        "artifacts": artifacts,
        "note": "Remote hardware capacity is not inferred from reachability; verify it inside the remote runtime.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(os.getenv("SPECTER_ROOT", r"C:\specter\Core")))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true", help="Require local gateway, HF health, and SQLite WAL")
    args = parser.parse_args()
    report = build_report(args.root)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if not args.strict:
        return 0
    required = [
        report["probes"]["local_8080_health"].get("ok") or report["probes"]["local_18088_health"].get("ok"),
        report["probes"]["hf_space_health"].get("ok"),
        report["sqlite"].get("wal_verified"),
    ]
    return 0 if all(required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
