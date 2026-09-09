# -*- coding: utf-8 -*-
"""SPECTER Core v3 federation liveness registry.

Evidence-driven properties:
- ACTIVE is granted only after a successful probe.
- Failed probes persist OFFLINE instead of leaving stale ACTIVE rows behind.
- ACTIVE rows expire to STALE after peer_ttl_s.
- SQLite WAL is enabled and verified.
- Federation status is derived from current peer state.
- Hardware/model capabilities are declared only through explicit configuration.
- Verified capabilities are tracked separately from advertised/declared capabilities
  so resource selection can require current evidence by default.

Python 3.11+. Standard library only.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request

DEFAULT_SPECTER_ROOT = Path(os.getenv("SPECTER_ROOT", r"C:\specter\Core"))
DEFAULT_DB_PATH = Path(
    os.getenv(
        "SPECTER_FABRIC_DB",
        str(DEFAULT_SPECTER_ROOT / "storage" / "specter_fabric.sqlite3"),
    )
)
DEFAULT_HF_SPACE_URL = os.getenv(
    "SPECTER_HF_SPACE_URL", "https://pintograndao-hermes-bridge.hf.space"
).rstrip("/")
DEFAULT_LOCAL_URL = os.getenv("SPECTER_LOCAL_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_HERMES_HOST = os.getenv("SPECTER_HERMES_HOST", "127.0.0.1")
DEFAULT_HERMES_PORT = int(os.getenv("SPECTER_HERMES_PORT", "51463"))

PeerStatus = str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    healthy: bool
    latency_ms: float
    error: str | None = None
    status_code: int | None = None
    evidence: Mapping[str, Any] | None = None


HttpProbe = Callable[[str, float], ProbeResult]
TcpProbe = Callable[[str, int, float], ProbeResult]
Clock = Callable[[], float]


def _json_list_from_env(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be a JSON array of strings: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise RuntimeError(f"{name} must be a JSON array of non-empty strings")
    return [x.strip() for x in value]


def default_http_probe(url: str, timeout_s: float) -> ProbeResult:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SpecterCore/3 federation-health"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            latency_ms = (time.perf_counter() - started) * 1000.0
            status = int(getattr(response, "status", response.getcode()))
            content_type = response.headers.get("Content-Type", "")
            healthy = 200 <= status < 400
            return ProbeResult(
                healthy=healthy,
                latency_ms=latency_ms,
                status_code=status,
                error=None if healthy else f"unhealthy HTTP status {status}",
                evidence={"content_type": content_type},
            )
    except urllib.error.HTTPError as exc:
        return ProbeResult(
            healthy=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            status_code=int(exc.code),
            error=f"HTTP {exc.code}: {exc.reason}",
        )
    except Exception as exc:
        return ProbeResult(
            healthy=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=f"{type(exc).__name__}: {exc}",
        )


def default_tcp_probe(host: str, port: int, timeout_s: float) -> ProbeResult:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return ProbeResult(
                healthy=True,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                evidence={"transport": "tcp"},
            )
    except Exception as exc:
        return ProbeResult(
            healthy=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=f"{type(exc).__name__}: {exc}",
        )


class SpecterFederationHub:
    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        local_url: str = DEFAULT_LOCAL_URL,
        hf_space_url: str = DEFAULT_HF_SPACE_URL,
        hermes_host: str = DEFAULT_HERMES_HOST,
        hermes_port: int = DEFAULT_HERMES_PORT,
        peer_ttl_s: float = 90.0,
        http_timeout_s: float = 3.0,
        tcp_timeout_s: float = 1.0,
        http_probe: HttpProbe = default_http_probe,
        tcp_probe: TcpProbe = default_tcp_probe,
        clock: Clock = time.time,
        declared_capabilities: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        if peer_ttl_s <= 0:
            raise ValueError("peer_ttl_s must be > 0")
        if http_timeout_s <= 0 or tcp_timeout_s <= 0:
            raise ValueError("probe timeouts must be > 0")
        if not (1 <= int(hermes_port) <= 65535):
            raise ValueError("hermes_port must be in 1..65535")

        self.db_path = Path(db_path)
        self.local_url = local_url.rstrip("/")
        self.hf_space_url = hf_space_url.rstrip("/")
        self.hermes_host = hermes_host
        self.hermes_port = int(hermes_port)
        self.peer_ttl_s = float(peer_ttl_s)
        self.http_timeout_s = float(http_timeout_s)
        self.tcp_timeout_s = float(tcp_timeout_s)
        self.http_probe = http_probe
        self.tcp_probe = tcp_probe
        self.clock = clock

        env_caps = {
            "specter-local-core": _json_list_from_env("SPECTER_LOCAL_CAPABILITIES"),
            "hermes-desktop-k3": _json_list_from_env("SPECTER_HERMES_CAPABILITIES"),
            "huggingface-vps-cluster": _json_list_from_env("SPECTER_HF_CAPABILITIES"),
        }
        supplied = declared_capabilities or {}
        self.declared_capabilities: dict[str, list[str]] = {
            peer_id: sorted(set(env_caps.get(peer_id, [])) | set(supplied.get(peer_id, [])))
            for peer_id in env_caps
        }
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back work, then release the Windows file handle."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            mode = str(row[0]).lower() if row else ""
            if mode != "wal":
                raise RuntimeError(f"failed to enable SQLite WAL mode: {mode!r}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS federation_peers (
                    peer_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    last_seen REAL NOT NULL DEFAULT 0,
                    checked_at REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    latency_ms REAL,
                    status_code INTEGER,
                    last_error TEXT,
                    evidence TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            existing = {row[1] for row in conn.execute("PRAGMA table_info(federation_peers)")}
            migrations = {
                "checked_at": "REAL NOT NULL DEFAULT 0",
                "latency_ms": "REAL",
                "status_code": "INTEGER",
                "last_error": "TEXT",
                "evidence": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, ddl in migrations.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE federation_peers ADD COLUMN {name} {ddl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_federation_peers_status_checked "
                "ON federation_peers(status, checked_at)"
            )

    def journal_mode(self) -> str:
        with self._connection() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()

    def _capabilities(self, peer_id: str, observed: Iterable[str]) -> list[str]:
        return sorted(set(observed) | set(self.declared_capabilities.get(peer_id, [])))

    def update_peer(
        self,
        *,
        peer_id: str,
        role: str,
        endpoint: str,
        capabilities: Sequence[str],
        probe: ProbeResult,
        verified_capabilities: Sequence[str] | None = None,
        declared_capabilities: Sequence[str] | None = None,
    ) -> None:
        checked_at = float(self.clock())
        status: PeerStatus = "ACTIVE" if probe.healthy else "OFFLINE"
        last_seen_candidate = checked_at if probe.healthy else 0.0
        evidence = dict(probe.evidence or {})
        if verified_capabilities is not None:
            evidence["verified_capabilities"] = (
                sorted(set(verified_capabilities)) if probe.healthy else []
            )
        if declared_capabilities is not None:
            evidence["declared_capabilities"] = sorted(set(declared_capabilities))
        evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        capabilities_json = json.dumps(sorted(set(capabilities)), ensure_ascii=False)

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO federation_peers(
                    peer_id, role, endpoint, last_seen, checked_at, status,
                    capabilities, latency_ms, status_code, last_error, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    role=excluded.role,
                    endpoint=excluded.endpoint,
                    last_seen=CASE
                        WHEN excluded.status='ACTIVE' THEN excluded.last_seen
                        ELSE federation_peers.last_seen
                    END,
                    checked_at=excluded.checked_at,
                    status=excluded.status,
                    capabilities=excluded.capabilities,
                    latency_ms=excluded.latency_ms,
                    status_code=excluded.status_code,
                    last_error=excluded.last_error,
                    evidence=excluded.evidence
                """,
                (
                    peer_id,
                    role,
                    endpoint,
                    last_seen_candidate,
                    checked_at,
                    status,
                    capabilities_json,
                    float(probe.latency_ms),
                    probe.status_code,
                    probe.error,
                    evidence_json,
                ),
            )

    def register_peer(self, peer_id: str, role: str, endpoint: str, capabilities: list[str]) -> None:
        """Compatibility API: explicitly registers a caller-asserted live peer."""
        self.update_peer(
            peer_id=peer_id,
            role=role,
            endpoint=endpoint,
            capabilities=capabilities,
            probe=ProbeResult(healthy=True, latency_ms=0.0, evidence={"source": "explicit_register"}),
            verified_capabilities=[],
            declared_capabilities=capabilities,
        )

    def expire_stale_peers(self) -> int:
        cutoff = float(self.clock()) - self.peer_ttl_s
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE federation_peers
                   SET status='STALE',
                       last_error=COALESCE(last_error, 'liveness TTL expired')
                 WHERE status='ACTIVE' AND checked_at < ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field, fallback in (("capabilities", []), ("evidence", {})):
            try:
                result[field] = json.loads(result.get(field) or json.dumps(fallback))
            except json.JSONDecodeError:
                result[field] = fallback

        evidence = result["evidence"] if isinstance(result["evidence"], dict) else {}
        for field in ("verified_capabilities", "declared_capabilities"):
            value = evidence.get(field, [])
            result[field] = (
                sorted(set(value))
                if isinstance(value, list) and all(isinstance(item, str) for item in value)
                else []
            )
        return result

    def get_peers(self) -> list[dict[str, Any]]:
        self.expire_stale_peers()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM federation_peers ORDER BY checked_at DESC, peer_id"
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_active_peers(self) -> list[dict[str, Any]]:
        self.expire_stale_peers()
        cutoff = float(self.clock()) - self.peer_ttl_s
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM federation_peers
                 WHERE status='ACTIVE' AND checked_at >= ?
                 ORDER BY checked_at DESC, peer_id
                """,
                (cutoff,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def select_active_peers(
        self,
        required_capabilities: Sequence[str] = (),
        *,
        allow_declared: bool = False,
    ) -> list[dict[str, Any]]:
        """Return ACTIVE peers matching capability requirements.

        By default only capabilities verified by the current liveness evidence are
        eligible. Set allow_declared=True only when caller policy explicitly
        accepts advertised or operator-declared capability claims.
        """
        required = {item.strip() for item in required_capabilities if item.strip()}
        field = "capabilities" if allow_declared else "verified_capabilities"
        return [
            peer
            for peer in self.get_active_peers()
            if required.issubset(set(peer.get(field, [])))
        ]

    @staticmethod
    def _federation_status(peers: Sequence[Mapping[str, Any]]) -> str:
        active = sum(1 for peer in peers if peer.get("status") == "ACTIVE")
        if active == 0:
            return "OFFLINE"
        if active == len(peers) and len(peers) > 0:
            return "VIVO"
        return "DEGRADED"

    def sync_federation(self) -> dict[str, Any]:
        local_probe = self.http_probe(f"{self.local_url}/health", self.http_timeout_s)
        self.update_peer(
            peer_id="specter-local-core",
            role="primary_coordinator",
            endpoint=self.local_url,
            capabilities=self._capabilities(
                "specter-local-core", ["http_health", "sqlite_wal"]
            ),
            probe=local_probe,
            verified_capabilities=["http_health", "sqlite_wal"],
            declared_capabilities=self.declared_capabilities["specter-local-core"],
        )

        hermes_probe = self.tcp_probe(self.hermes_host, self.hermes_port, self.tcp_timeout_s)
        self.update_peer(
            peer_id="hermes-desktop-k3",
            role="deep_reasoning_sentinel",
            endpoint=f"tcp://{self.hermes_host}:{self.hermes_port}",
            capabilities=self._capabilities("hermes-desktop-k3", ["tcp_endpoint"]),
            probe=hermes_probe,
            verified_capabilities=["tcp_endpoint"],
            declared_capabilities=self.declared_capabilities["hermes-desktop-k3"],
        )

        hf_probe = self.http_probe(f"{self.hf_space_url}/health", self.http_timeout_s)
        self.update_peer(
            peer_id="huggingface-vps-cluster",
            role="cloud_edge",
            endpoint=self.hf_space_url,
            capabilities=self._capabilities(
                "huggingface-vps-cluster", ["http_health", "gradio_hub"]
            ),
            probe=hf_probe,
            verified_capabilities=["http_health"],
            declared_capabilities=self.declared_capabilities["huggingface-vps-cluster"],
        )

        peers = self.get_peers()
        active_peers = [peer for peer in peers if peer["status"] == "ACTIVE"]
        return {
            "federation_status": self._federation_status(peers),
            "active_peer_count": len(active_peers),
            "peer_count": len(peers),
            "sqlite_journal_mode": self.journal_mode(),
            "checked_at": float(self.clock()),
            "peers": peers,
        }


def main() -> int:
    hub = SpecterFederationHub()
    result = hub.sync_federation()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["active_peer_count"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
