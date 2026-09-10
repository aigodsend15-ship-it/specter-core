from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import hashlib
import json
import os
import sqlite3
import time
import urllib.request
import uuid


class WorkerStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


@dataclass(frozen=True)
class RemoteWorker:
    worker_id: str
    provider: str
    transport: str
    endpoint_url: str
    quota_domain: str
    capabilities: tuple[str, ...]
    authorized: bool = False
    enabled: bool = True
    max_parallel: int = 1
    cost_per_hour_usd: float = 0.0
    api_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RemoteWorker":
        return cls(
            worker_id=str(value["worker_id"]),
            provider=str(value["provider"]),
            transport=str(value["transport"]),
            endpoint_url=str(value["endpoint_url"]).rstrip("/"),
            quota_domain=str(value["quota_domain"]),
            capabilities=tuple(str(x) for x in value.get("capabilities", ())),
            authorized=bool(value.get("authorized", False)),
            enabled=bool(value.get("enabled", True)),
            max_parallel=max(1, int(value.get("max_parallel", 1))),
            cost_per_hour_usd=max(0.0, float(value.get("cost_per_hour_usd", 0.0))),
            api_name=(str(value["api_name"]) if value.get("api_name") else None),
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["capabilities"] = list(self.capabilities)
        return out


@dataclass(frozen=True)
class WorkerHealth:
    worker_id: str
    status: WorkerStatus
    checked_at: float
    latency_ms: float
    detail: str = ""


@dataclass(frozen=True)
class Lease:
    lease_id: str
    worker_id: str
    task_id: str
    lease_epoch: int
    expires_at: float


class RemoteWorkerRegistry:
    """Atomic JSON registry. Credentials and tokens are never persisted here."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "workers": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid_registry")
        data.setdefault("version", 1)
        data.setdefault("workers", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def upsert(self, worker: RemoteWorker) -> None:
        data = self._read()
        data["workers"][worker.worker_id] = worker.to_dict()
        self._write(data)

    def get(self, worker_id: str) -> RemoteWorker:
        row = self._read()["workers"].get(worker_id)
        if row is None:
            raise KeyError(worker_id)
        return RemoteWorker.from_dict(row)

    def list(self) -> list[RemoteWorker]:
        rows = self._read()["workers"]
        return [RemoteWorker.from_dict(rows[key]) for key in sorted(rows)]


class LeaseStore:
    """SQLite WAL leases with monotonic fencing epochs."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            yield con
        finally:
            con.close()

    def _init_schema(self) -> None:
        with self.connection() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS remote_worker_leases(
                    worker_id TEXT NOT NULL,
                    lease_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    lease_epoch INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_epochs(
                    worker_id TEXT PRIMARY KEY,
                    epoch INTEGER NOT NULL
                )
                """
            )

    def active_count(self, worker_id: str, now: Optional[float] = None) -> int:
        current = time.time() if now is None else float(now)
        with self.connection() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM remote_worker_leases "
                "WHERE worker_id=? AND expires_at>?",
                (worker_id, current),
            ).fetchone()
            return int(row["n"])

    def acquire(
        self,
        worker: RemoteWorker,
        task_id: str,
        ttl_seconds: float = 120.0,
    ) -> Lease:
        now = time.time()
        expires = now + max(5.0, float(ttl_seconds))
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM remote_worker_leases WHERE worker_id=? AND expires_at<=?",
                (worker.worker_id, now),
            )
            row = con.execute(
                "SELECT COUNT(*) AS n FROM remote_worker_leases "
                "WHERE worker_id=? AND expires_at>?",
                (worker.worker_id, now),
            ).fetchone()
            if int(row["n"]) >= worker.max_parallel:
                con.rollback()
                raise RuntimeError("worker_capacity_exhausted")
            epoch_row = con.execute(
                "SELECT epoch FROM worker_epochs WHERE worker_id=?",
                (worker.worker_id,),
            ).fetchone()
            epoch = 1 if epoch_row is None else int(epoch_row["epoch"]) + 1
            con.execute(
                "INSERT INTO worker_epochs(worker_id, epoch) VALUES(?,?) "
                "ON CONFLICT(worker_id) DO UPDATE SET epoch=excluded.epoch",
                (worker.worker_id, epoch),
            )
            lease_id = uuid.uuid4().hex
            con.execute(
                "INSERT INTO remote_worker_leases("
                "worker_id,lease_id,task_id,lease_epoch,expires_at,created_at"
                ") VALUES(?,?,?,?,?,?)",
                (worker.worker_id, lease_id, task_id, epoch, expires, now),
            )
            con.commit()
        return Lease(lease_id, worker.worker_id, task_id, epoch, expires)

    def release(self, lease_id: str) -> bool:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "DELETE FROM remote_worker_leases WHERE lease_id=?", (lease_id,)
            )
            changed = cur.rowcount > 0
            con.commit()
            return changed


class QuotaDomainLedger:
    """
    Tracks provider saturation without pausing unrelated capacity.

    Saturating one quota domain causes immediate failover to another routeable
    domain. retry_after only controls when the saturated domain may be probed again.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            yield con
        finally:
            con.close()

    def _init_schema(self) -> None:
        with self.connection() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_domains(
                    quota_domain TEXT PRIMARY KEY,
                    saturated INTEGER NOT NULL,
                    retry_after REAL NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def mark_saturated(
        self,
        quota_domain: str,
        reason: str,
        probe_after_seconds: float = 900.0,
    ) -> None:
        now = time.time()
        with self.connection() as con:
            con.execute(
                """
                INSERT INTO quota_domains(
                    quota_domain,saturated,retry_after,reason,updated_at
                ) VALUES(?,1,?,?,?)
                ON CONFLICT(quota_domain) DO UPDATE SET
                    saturated=1,
                    retry_after=excluded.retry_after,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (
                    quota_domain,
                    now + max(30.0, float(probe_after_seconds)),
                    reason[:500],
                    now,
                ),
            )

    def mark_healthy(self, quota_domain: str) -> None:
        now = time.time()
        with self.connection() as con:
            con.execute(
                """
                INSERT INTO quota_domains(
                    quota_domain,saturated,retry_after,reason,updated_at
                ) VALUES(?,0,0,'',?)
                ON CONFLICT(quota_domain) DO UPDATE SET
                    saturated=0,retry_after=0,reason='',updated_at=excluded.updated_at
                """,
                (quota_domain, now),
            )

    def routeable(self, quota_domain: str, now: Optional[float] = None) -> bool:
        current = time.time() if now is None else float(now)
        with self.connection() as con:
            row = con.execute(
                "SELECT saturated,retry_after FROM quota_domains WHERE quota_domain=?",
                (quota_domain,),
            ).fetchone()
        if row is None or not bool(row["saturated"]):
            return True
        return current >= float(row["retry_after"])


class ImmediateFailoverScheduler:
    def __init__(
        self,
        registry: RemoteWorkerRegistry,
        leases: LeaseStore,
        quota_ledger: QuotaDomainLedger,
    ):
        self.registry = registry
        self.leases = leases
        self.quota_ledger = quota_ledger

    def candidates(
        self,
        capability: str,
        *,
        allow_paid: bool = False,
        max_cost_per_hour_usd: float = 0.0,
    ) -> list[RemoteWorker]:
        out: list[RemoteWorker] = []
        for worker in self.registry.list():
            if not worker.authorized or not worker.enabled:
                continue
            if capability not in worker.capabilities:
                continue
            if not self.quota_ledger.routeable(worker.quota_domain):
                continue
            if not allow_paid and worker.cost_per_hour_usd > 0:
                continue
            if allow_paid and worker.cost_per_hour_usd > max(0.0, max_cost_per_hour_usd):
                continue
            if self.leases.active_count(worker.worker_id) >= worker.max_parallel:
                continue
            out.append(worker)
        out.sort(key=lambda w: (w.cost_per_hour_usd, -w.max_parallel, w.worker_id))
        return out

    def acquire_best(
        self,
        task_id: str,
        capability: str,
        *,
        allow_paid: bool = False,
        max_cost_per_hour_usd: float = 0.0,
        ttl_seconds: float = 120.0,
    ) -> Lease:
        for worker in self.candidates(
            capability,
            allow_paid=allow_paid,
            max_cost_per_hour_usd=max_cost_per_hour_usd,
        ):
            try:
                return self.leases.acquire(worker, task_id, ttl_seconds)
            except RuntimeError:
                continue
        raise RuntimeError("no_authorized_remote_capacity")


def probe_worker(worker: RemoteWorker, timeout: float = 10.0) -> WorkerHealth:
    if not worker.enabled:
        return WorkerHealth(worker.worker_id, WorkerStatus.DISABLED, time.time(), 0.0)
    if not worker.authorized:
        return WorkerHealth(
            worker.worker_id,
            WorkerStatus.DEGRADED,
            time.time(),
            0.0,
            "not_authorized",
        )
    started = time.perf_counter()
    request = urllib.request.Request(
        worker.endpoint_url.rstrip("/") + "/",
        headers={"User-Agent": "SpecterRemoteFabric/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200))
            response.read(256)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if 200 <= status_code < 500:
            return WorkerHealth(
                worker.worker_id,
                WorkerStatus.HEALTHY,
                time.time(),
                latency_ms,
            )
        return WorkerHealth(
            worker.worker_id,
            WorkerStatus.DEGRADED,
            time.time(),
            latency_ms,
            f"http_{status_code}",
        )
    except Exception as exc:
        return WorkerHealth(
            worker.worker_id,
            WorkerStatus.OFFLINE,
            time.time(),
            (time.perf_counter() - started) * 1000.0,
            type(exc).__name__,
        )


def classify_capacity_failure(text: str = "", status_code: Optional[int] = None) -> str:
    value = (text or "").lower()
    if status_code == 429:
        return "saturated"
    if any(
        term in value
        for term in (
            "quota reached",
            "quota exceeded",
            "rate limit",
            "usage limit",
            "resource exhausted",
            "too many requests",
            "capacity exhausted",
            "individual quota reached",
        )
    ):
        return "saturated"
    if status_code in (401, 403):
        return "auth_or_entitlement"
    if status_code is not None and 500 <= status_code <= 599:
        return "transient"
    return "unknown"


def worker_manifest_hash(worker: RemoteWorker) -> str:
    payload = json.dumps(
        worker.to_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def bootstrap_registry(path: Path) -> RemoteWorkerRegistry:
    registry = RemoteWorkerRegistry(path)
    if not registry.list():
        registry.upsert(
            RemoteWorker(
                worker_id="hf-hermes-bridge",
                provider="huggingface",
                transport="gradio_space",
                endpoint_url="https://pintograndao-hermes-bridge.hf.space",
                quota_domain="hf-space:pintograndao/hermes-bridge",
                capabilities=("chat", "reasoning", "agent"),
                authorized=True,
                enabled=True,
                max_parallel=1,
                cost_per_hour_usd=0.0,
                api_name=None,
                metadata={
                    "space_repo": "pintograndao/hermes-bridge",
                    "dispatch_contract": "discover_before_use",
                },
            )
        )
    return registry
