"""SPECTER gateway auto-healing supervisor.

This module is designed to run as a sidecar/supervisor for OpenAI-compatible
HTTP gateways. It monitors configured health endpoints, drives a per-endpoint
circuit breaker, persists operational metrics in SQLite/WAL, atomically emits
JSON health alerts, executes explicit restart commands without a shell, and
selects the highest-priority healthy endpoint for graceful failover.

Python: 3.11+
Runtime dependency: aiohttp>=3.9
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import aiohttp

__all__ = [
    "AioHttpHealthProbe",
    "AlertStore",
    "AutoHealer",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "EndpointConfig",
    "FailureKind",
    "HealthProbe",
    "HealthRepository",
    "HealthResult",
    "HealerConfig",
    "RestartExecutor",
    "RestartResult",
    "SubprocessRestartExecutor",
    "configure_structured_logging",
]

UTC = timezone.utc
LOGGER_NAME = "specter.auto_healer"


def utc_now_iso() -> str:
    """Return an RFC 3339-like UTC timestamp with timezone information."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


class CircuitState(str, Enum):
    """Circuit-breaker state."""

    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"
    OPEN = "OPEN"


class FailureKind(str, Enum):
    """Normalized health-probe failure classes."""

    NONE = "none"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    HTTP_STATUS = "http_status"
    CIRCUIT_OPEN = "circuit_open"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    """Circuit breaker tuning parameters."""

    failure_threshold: int = 3
    recovery_timeout_s: float = 60.0
    half_open_success_threshold: int = 1

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.recovery_timeout_s <= 0:
            raise ValueError("recovery_timeout_s must be > 0")
        if self.half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be >= 1")


class CircuitBreaker:
    """Deterministic CLOSED/HALF_OPEN/OPEN circuit breaker.

    The breaker uses a monotonic clock so wall-clock changes cannot prematurely
    release an OPEN circuit.
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or CircuitBreakerConfig()
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_successes = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        """Return the current state without mutating it."""

        return self._state

    @property
    def consecutive_failures(self) -> int:
        """Return the current consecutive failure count."""

        return self._consecutive_failures

    def allow_request(self) -> bool:
        """Return whether a probe may execute, transitioning OPEN→HALF_OPEN."""

        if self._state is not CircuitState.OPEN:
            return True

        assert self._opened_at is not None
        if self._clock() - self._opened_at >= self.config.recovery_timeout_s:
            self._state = CircuitState.HALF_OPEN
            self._half_open_successes = 0
            return True
        return False

    def record_success(self) -> None:
        """Record a successful probe and update the breaker state."""

        if self._state is CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.config.half_open_success_threshold:
                self._close()
            return

        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed probe and OPEN the circuit when required."""

        if self._state is CircuitState.HALF_OPEN:
            self._open()
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._half_open_successes = 0

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._consecutive_failures = 0
        self._half_open_successes = 0


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    """Configuration for one gateway endpoint."""

    name: str
    base_url: str
    health_path: str = "/health"
    priority: int = 100
    restart_command: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("endpoint name cannot be empty")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if not self.health_path.startswith("/"):
            raise ValueError("health_path must start with '/'")

    @property
    def health_url(self) -> str:
        """Return the full health-check URL."""

        return f"{self.base_url.rstrip('/')}{self.health_path}"


@dataclass(frozen=True, slots=True)
class HealerConfig:
    """Top-level auto-healer configuration."""

    health_interval_s: float = 30.0
    request_timeout_s: float = 5.0
    restart_cooldown_s: float = 60.0
    restart_timeout_s: float = 30.0
    sqlite_path: Path = Path(r"C:\specter\Core\data\auto_healer.db")
    alerts_path: Path = Path(r"C:\specter\Core\exports\health_alerts.json")
    log_path: Path = Path(r"C:\specter\Core\logs\auto_healer.log")
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    max_alerts: int = 500
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()

    def __post_init__(self) -> None:
        if self.health_interval_s <= 0:
            raise ValueError("health_interval_s must be > 0")
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be > 0")
        if self.restart_cooldown_s < 0:
            raise ValueError("restart_cooldown_s must be >= 0")
        if self.restart_timeout_s <= 0:
            raise ValueError("restart_timeout_s must be > 0")
        if self.log_max_bytes < 1:
            raise ValueError("log_max_bytes must be >= 1")
        if self.log_backup_count < 1:
            raise ValueError("log_backup_count must be >= 1")
        if self.max_alerts < 1:
            raise ValueError("max_alerts must be >= 1")


@dataclass(frozen=True, slots=True)
class HealthResult:
    """Result of a single health probe."""

    healthy: bool
    latency_ms: float
    status_code: int | None = None
    error: str | None = None
    failure_kind: FailureKind = FailureKind.NONE


@dataclass(frozen=True, slots=True)
class RestartResult:
    """Result of invoking an endpoint's restart action."""

    success: bool
    returncode: int | None = None
    error: str | None = None


class HealthProbe(Protocol):
    """Protocol for health-check implementations."""

    async def start(self) -> None:
        """Initialize resources."""

    async def check(self, endpoint: EndpointConfig, timeout_s: float) -> HealthResult:
        """Probe an endpoint."""

    async def close(self) -> None:
        """Release resources."""


class RestartExecutor(Protocol):
    """Protocol for restart implementations."""

    async def restart(self, endpoint: EndpointConfig, timeout_s: float) -> RestartResult:
        """Execute the restart action for an endpoint."""


class AioHttpHealthProbe:
    """aiohttp-based health probe with an explicit total request timeout."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._logger = logger or logging.getLogger(LOGGER_NAME)

    async def start(self) -> None:
        if self._session is not None and not self._session.closed:
            return
        connector = aiohttp.TCPConnector(limit=16, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            raise_for_status=False,
            trust_env=False,
        )

    async def check(self, endpoint: EndpointConfig, timeout_s: float) -> HealthResult:
        if self._session is None or self._session.closed:
            raise RuntimeError("AioHttpHealthProbe.start() must be called before check()")

        started = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        try:
            async with self._session.get(
                endpoint.health_url,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                latency_ms = (time.perf_counter() - started) * 1000.0
                healthy = 200 <= response.status < 300
                if healthy:
                    return HealthResult(
                        healthy=True,
                        latency_ms=latency_ms,
                        status_code=response.status,
                    )
                return HealthResult(
                    healthy=False,
                    latency_ms=latency_ms,
                    status_code=response.status,
                    error=f"unhealthy HTTP status {response.status}",
                    failure_kind=FailureKind.HTTP_STATUS,
                )
        except asyncio.TimeoutError:
            return HealthResult(
                healthy=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"health check timed out after {timeout_s:.3f}s",
                failure_kind=FailureKind.TIMEOUT,
            )
        except aiohttp.ClientConnectionError as exc:
            return HealthResult(
                healthy=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"connection error: {exc}",
                failure_kind=FailureKind.CONNECTION,
            )
        except aiohttp.ClientError as exc:
            return HealthResult(
                healthy=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"aiohttp error: {exc}",
                failure_kind=FailureKind.INTERNAL,
            )
        except Exception as exc:  # defensive boundary around third-party/network code
            self._logger.exception(
                "unexpected health probe failure",
                extra={"event": "probe_internal_error", "endpoint": endpoint.name},
            )
            return HealthResult(
                healthy=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"unexpected probe error: {type(exc).__name__}: {exc}",
                failure_kind=FailureKind.INTERNAL,
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class SubprocessRestartExecutor:
    """Execute a configured restart command without invoking a shell.

    The command should be a supervisor action (for example, a Windows service
    restart, NSSM command, systemd action, or a trusted wrapper script). The
    healer intentionally does not guess process IDs or kill arbitrary processes.
    """

    async def restart(self, endpoint: EndpointConfig, timeout_s: float) -> RestartResult:
        if not endpoint.restart_command:
            return RestartResult(success=False, error="restart command is not configured")

        try:
            process = await asyncio.create_subprocess_exec(
                *endpoint.restart_command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return RestartResult(
                    success=False,
                    returncode=process.returncode,
                    error=f"restart command timed out after {timeout_s:.3f}s",
                )

            stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            if process.returncode == 0:
                return RestartResult(success=True, returncode=0)
            return RestartResult(
                success=False,
                returncode=process.returncode,
                error=stderr_text[-2000:] or f"restart command exited with {process.returncode}",
            )
        except (OSError, ValueError) as exc:
            return RestartResult(
                success=False,
                error=f"failed to launch restart command: {type(exc).__name__}: {exc}",
            )


class HealthRepository:
    """SQLite/WAL persistence for cumulative health metrics."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS endpoint_metrics (
        endpoint TEXT PRIMARY KEY,
        uptime_seconds REAL NOT NULL DEFAULT 0,
        downtime_seconds REAL NOT NULL DEFAULT 0,
        restart_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_checked_at TEXT,
        last_state TEXT NOT NULL DEFAULT 'UNKNOWN',
        last_status_code INTEGER,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            mode_row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            mode = str(mode_row[0]).lower() if mode_row is not None else ""
            if mode != "wal":
                raise RuntimeError(f"failed to enable SQLite WAL mode; journal_mode={mode!r}")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(self._SCHEMA)
            connection.execute("PRAGMA user_version=1")

    def record_health(
        self,
        *,
        endpoint: str,
        healthy: bool,
        elapsed_s: float,
        state: CircuitState,
        status_code: int | None,
        consecutive_failures: int,
        error: str | None,
    ) -> None:
        """Persist a health sample and accumulate uptime/downtime."""

        elapsed = max(0.0, elapsed_s)
        now = utc_now_iso()
        uptime_delta = elapsed if healthy else 0.0
        downtime_delta = 0.0 if healthy else elapsed

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO endpoint_metrics(endpoint, updated_at)
                VALUES (?, ?)
                ON CONFLICT(endpoint) DO NOTHING
                """,
                (endpoint, now),
            )
            connection.execute(
                """
                UPDATE endpoint_metrics
                SET uptime_seconds = uptime_seconds + ?,
                    downtime_seconds = downtime_seconds + ?,
                    last_error = CASE
                        WHEN ? THEN NULL
                        WHEN ? IS NOT NULL THEN ?
                        ELSE last_error
                    END,
                    last_checked_at = ?,
                    last_state = ?,
                    last_status_code = ?,
                    consecutive_failures = ?,
                    updated_at = ?
                WHERE endpoint = ?
                """,
                (
                    uptime_delta,
                    downtime_delta,
                    1 if healthy else 0,
                    error,
                    error,
                    now,
                    state.value,
                    status_code,
                    consecutive_failures,
                    now,
                    endpoint,
                ),
            )

    def record_restart(self, endpoint: str, *, error: str | None = None) -> None:
        """Increment restart_count for an attempted restart."""

        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO endpoint_metrics(endpoint, restart_count, last_error, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    restart_count = restart_count + 1,
                    last_error = COALESCE(excluded.last_error, endpoint_metrics.last_error),
                    updated_at = excluded.updated_at
                """,
                (endpoint, error, now),
            )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return all endpoint metrics keyed by endpoint name."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM endpoint_metrics ORDER BY endpoint"
            ).fetchall()
        return {str(row["endpoint"]): dict(row) for row in rows}

    def journal_mode(self) -> str:
        """Return SQLite's active journal mode."""

        with self._lock, self._connect() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()


class AlertStore:
    """Atomic bounded JSON alert/snapshot writer."""

    def __init__(self, path: Path, *, max_alerts: int = 500) -> None:
        self.path = Path(path)
        self.max_alerts = max_alerts
        self._lock = asyncio.Lock()

    async def publish(
        self,
        *,
        active_endpoint: str | None,
        endpoints: Mapping[str, Mapping[str, Any]],
        alert: Mapping[str, Any] | None = None,
    ) -> None:
        """Atomically publish health state and optionally append one alert."""

        async with self._lock:
            await asyncio.to_thread(
                self._publish_sync,
                active_endpoint,
                dict(endpoints),
                dict(alert) if alert is not None else None,
            )

    def _publish_sync(
        self,
        active_endpoint: str | None,
        endpoints: dict[str, Mapping[str, Any]],
        alert: dict[str, Any] | None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_alerts: list[dict[str, Any]] = []
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                raw_alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
                if isinstance(raw_alerts, list):
                    existing_alerts = [x for x in raw_alerts if isinstance(x, dict)]
            except (OSError, json.JSONDecodeError):
                existing_alerts = []

        if alert is not None:
            existing_alerts.append(alert)
        existing_alerts = existing_alerts[-self.max_alerts :]

        payload = {
            "schema_version": 1,
            "updated_at": utc_now_iso(),
            "active_endpoint": active_endpoint,
            "endpoints": endpoints,
            "alerts": existing_alerts,
        }

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


class _JsonFormatter(logging.Formatter):
    _STANDARD_FIELDS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_structured_logging(
    path: Path,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure one rotating JSON-lines logger without duplicate handlers."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    resolved = str(path.resolve())
    for existing_handler in list(logger.handlers):
        target = getattr(existing_handler, "_specter_target", None)
        if target == resolved:
            return logger
        if target is not None:
            logger.removeHandler(existing_handler)
            existing_handler.close()

    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(_JsonFormatter())
    setattr(handler, "_specter_target", resolved)
    logger.addHandler(handler)
    return logger


@dataclass(slots=True)
class _EndpointRuntime:
    healthy: bool | None = None
    last_result: HealthResult | None = None
    last_checked_at: str | None = None
    last_restart_at: str | None = None


class AutoHealer:
    """Coordinate health checks, restarts, breaker state, metrics, and failover."""

    def __init__(
        self,
        endpoints: Sequence[EndpointConfig],
        *,
        config: HealerConfig | None = None,
        probe: HealthProbe | None = None,
        restart_executor: RestartExecutor | None = None,
        repository: HealthRepository | None = None,
        alert_store: AlertStore | None = None,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        names = [endpoint.name for endpoint in endpoints]
        if len(names) != len(set(names)):
            raise ValueError("endpoint names must be unique")

        self.config = config or HealerConfig()
        self.endpoints = tuple(sorted(endpoints, key=lambda item: (item.priority, item.name)))
        self._clock = clock
        self.logger = logger or configure_structured_logging(
            self.config.log_path,
            max_bytes=self.config.log_max_bytes,
            backup_count=self.config.log_backup_count,
        )
        self.probe = probe or AioHttpHealthProbe(logger=self.logger)
        self.restart_executor = restart_executor or SubprocessRestartExecutor()
        self.repository = repository or HealthRepository(self.config.sqlite_path)
        self.alert_store = alert_store or AlertStore(
            self.config.alerts_path, max_alerts=self.config.max_alerts
        )
        self._breakers = {
            endpoint.name: CircuitBreaker(self.config.circuit_breaker, clock=clock)
            for endpoint in self.endpoints
        }
        self._runtime = {endpoint.name: _EndpointRuntime() for endpoint in self.endpoints}
        initial_clock = self._clock()
        self._last_sample_clock = {endpoint.name: initial_clock for endpoint in self.endpoints}
        self._last_restart_clock = {endpoint.name: float("-inf") for endpoint in self.endpoints}
        self._active_endpoint: EndpointConfig | None = self.endpoints[0]
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._started = False

    @property
    def active_endpoint(self) -> EndpointConfig | None:
        """Return the selected healthy target, or None if none are healthy."""

        return self._active_endpoint

    def active_base_url(self) -> str | None:
        """Return the active base URL for proxy routing integration."""

        return self._active_endpoint.base_url if self._active_endpoint else None

    def resolve_target(self, path: str = "") -> str | None:
        """Resolve a request path against the current active gateway target."""

        if self._active_endpoint is None:
            return None
        suffix = path if path.startswith("/") or not path else f"/{path}"
        return f"{self._active_endpoint.base_url.rstrip('/')}{suffix}"

    async def start(self) -> None:
        """Initialize resources, run an immediate check, then start the 30s loop."""

        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        await self.probe.start()
        await self.run_once()
        self._loop_task = asyncio.create_task(self._monitor_loop(), name="specter-auto-healer")
        self.logger.info("auto healer started", extra={"event": "healer_started"})

    async def close(self) -> None:
        """Stop monitoring and release resources idempotently."""

        self._stop_event.set()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.probe.close()
        self._started = False
        self.logger.info("auto healer stopped", extra={"event": "healer_stopped"})

    async def __aenter__(self) -> "AutoHealer":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def run_forever(self) -> None:
        """Run until cancelled or close() is called."""

        await self.start()
        try:
            await self._stop_event.wait()
        finally:
            await self.close()

    async def _monitor_loop(self) -> None:
        next_run = self._clock() + self.config.health_interval_s
        try:
            while not self._stop_event.is_set():
                delay = max(0.0, next_run - self._clock())
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    pass

                await self.run_once()
                next_run += self.config.health_interval_s
                # If a check cycle stalls for longer than one full interval, avoid
                # a catch-up burst and schedule the next check from "now".
                if next_run <= self._clock():
                    next_run = self._clock() + self.config.health_interval_s
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception(
                "monitor loop crashed",
                extra={"event": "monitor_loop_crash"},
            )
            raise

    async def run_once(self) -> None:
        """Run one concurrent health-check cycle over all endpoints."""

        await asyncio.gather(*(self._check_endpoint(endpoint) for endpoint in self.endpoints))
        await self._reselect_active_endpoint()
        await self._publish_snapshot()

    async def _check_endpoint(self, endpoint: EndpointConfig) -> None:
        breaker = self._breakers[endpoint.name]
        runtime = self._runtime[endpoint.name]
        now_clock = self._clock()
        elapsed_s = max(0.0, now_clock - self._last_sample_clock[endpoint.name])
        self._last_sample_clock[endpoint.name] = now_clock

        previous_health = runtime.healthy
        if not breaker.allow_request():
            result = HealthResult(
                healthy=False,
                latency_ms=0.0,
                error="circuit is OPEN",
                failure_kind=FailureKind.CIRCUIT_OPEN,
            )
        else:
            result = await self.probe.check(endpoint, self.config.request_timeout_s)
            if result.healthy:
                breaker.record_success()
            else:
                breaker.record_failure()

        runtime.healthy = result.healthy
        runtime.last_result = result
        runtime.last_checked_at = utc_now_iso()

        await asyncio.to_thread(
            self.repository.record_health,
            endpoint=endpoint.name,
            healthy=result.healthy,
            elapsed_s=elapsed_s,
            state=breaker.state,
            status_code=result.status_code,
            consecutive_failures=breaker.consecutive_failures,
            error=None if result.failure_kind is FailureKind.CIRCUIT_OPEN else result.error,
        )

        if previous_health is not result.healthy:
            alert_type = "ENDPOINT_RECOVERED" if result.healthy else "ENDPOINT_UNHEALTHY"
            await self._emit_alert(
                alert_type,
                endpoint=endpoint,
                details={
                    "healthy": result.healthy,
                    "status_code": result.status_code,
                    "error": result.error,
                    "failure_kind": result.failure_kind.value,
                    "circuit_state": breaker.state.value,
                },
            )

        if not result.healthy and self._should_restart(endpoint, result):
            await self._restart(endpoint, result)

        self.logger.log(
            logging.INFO if result.healthy else logging.WARNING,
            "health check completed",
            extra={
                "event": "health_check",
                "endpoint": endpoint.name,
                "url": endpoint.health_url,
                "healthy": result.healthy,
                "status_code": result.status_code,
                "latency_ms": round(result.latency_ms, 3),
                "failure_kind": result.failure_kind.value,
                "circuit_state": breaker.state.value,
                "error": result.error,
            },
        )

    def _should_restart(self, endpoint: EndpointConfig, result: HealthResult) -> bool:
        if result.failure_kind is FailureKind.CIRCUIT_OPEN:
            return False
        since_restart = self._clock() - self._last_restart_clock[endpoint.name]
        if since_restart < self.config.restart_cooldown_s:
            return False
        # Timeouts/connection failures satisfy the explicit "not responding" case.
        # 5xx health responses are also treated as restart-worthy unhealthy states.
        return result.failure_kind in {
            FailureKind.TIMEOUT,
            FailureKind.CONNECTION,
            FailureKind.INTERNAL,
        } or (
            result.failure_kind is FailureKind.HTTP_STATUS
            and result.status_code is not None
            and result.status_code >= 500
        )

    async def _restart(self, endpoint: EndpointConfig, trigger: HealthResult) -> None:
        self._last_restart_clock[endpoint.name] = self._clock()
        result = await self.restart_executor.restart(endpoint, self.config.restart_timeout_s)
        self._runtime[endpoint.name].last_restart_at = utc_now_iso()
        await asyncio.to_thread(
            self.repository.record_restart,
            endpoint.name,
            error=None if result.success else result.error,
        )
        await self._emit_alert(
            "RESTART_SUCCEEDED" if result.success else "RESTART_FAILED",
            endpoint=endpoint,
            details={
                "trigger": trigger.failure_kind.value,
                "trigger_error": trigger.error,
                "success": result.success,
                "returncode": result.returncode,
                "error": result.error,
            },
        )
        self.logger.log(
            logging.INFO if result.success else logging.ERROR,
            "restart action completed",
            extra={
                "event": "restart",
                "endpoint": endpoint.name,
                "success": result.success,
                "returncode": result.returncode,
                "error": result.error,
            },
        )

    async def _reselect_active_endpoint(self) -> None:
        previous = self._active_endpoint
        selected: EndpointConfig | None = None
        for endpoint in self.endpoints:
            runtime = self._runtime[endpoint.name]
            if runtime.healthy and self._breakers[endpoint.name].state is not CircuitState.OPEN:
                selected = endpoint
                break
        self._active_endpoint = selected

        previous_name = previous.name if previous else None
        selected_name = selected.name if selected else None
        if previous_name != selected_name:
            await self._emit_alert(
                "ROUTE_CHANGED",
                endpoint=selected,
                details={"from": previous_name, "to": selected_name},
            )
            self.logger.warning(
                "active gateway route changed",
                extra={
                    "event": "route_changed",
                    "from_endpoint": previous_name,
                    "to_endpoint": selected_name,
                },
            )

    def _endpoint_snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for endpoint in self.endpoints:
            runtime = self._runtime[endpoint.name]
            result = runtime.last_result
            snapshot[endpoint.name] = {
                "base_url": endpoint.base_url,
                "health_url": endpoint.health_url,
                "priority": endpoint.priority,
                "healthy": runtime.healthy,
                "circuit_state": self._breakers[endpoint.name].state.value,
                "consecutive_failures": self._breakers[endpoint.name].consecutive_failures,
                "last_checked_at": runtime.last_checked_at,
                "last_restart_at": runtime.last_restart_at,
                "status_code": result.status_code if result else None,
                "latency_ms": round(result.latency_ms, 3) if result else None,
                "failure_kind": result.failure_kind.value if result else None,
                "last_error": result.error if result else None,
            }
        return snapshot

    async def _publish_snapshot(self) -> None:
        await self.alert_store.publish(
            active_endpoint=self._active_endpoint.name if self._active_endpoint else None,
            endpoints=self._endpoint_snapshot(),
        )

    async def _emit_alert(
        self,
        alert_type: str,
        *,
        endpoint: EndpointConfig | None,
        details: Mapping[str, Any],
    ) -> None:
        alert = {
            "timestamp": utc_now_iso(),
            "type": alert_type,
            "endpoint": endpoint.name if endpoint else None,
            "details": dict(details),
        }
        await self.alert_store.publish(
            active_endpoint=self._active_endpoint.name if self._active_endpoint else None,
            endpoints=self._endpoint_snapshot(),
            alert=alert,
        )

    async def metrics_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return persisted cumulative metrics without blocking the event loop."""

        return await asyncio.to_thread(self.repository.snapshot)


def _parse_restart_command(environment_name: str) -> tuple[str, ...]:
    raw = os.getenv(environment_name, "").strip()
    if not raw:
        raise RuntimeError(
            f"{environment_name} is required and must be a JSON argv array, "
            'for example ["powershell.exe","-NoProfile","-File","restart-8080.ps1"]'
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{environment_name} is not valid JSON: {exc}") from exc
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise RuntimeError(f"{environment_name} must be a non-empty JSON array of strings")
    return tuple(value)


def build_default_healer_from_env() -> AutoHealer:
    """Build the standard 8080/18088 SPECTER healer from environment variables.

    Required:
      SPECTER_RESTART_CMD_8080
      SPECTER_RESTART_CMD_18088

    Each restart command is a JSON argv array. No shell parsing is performed.
    """

    config = HealerConfig(
        health_interval_s=float(os.getenv("SPECTER_HEALTH_INTERVAL_S", "30")),
        request_timeout_s=float(os.getenv("SPECTER_HEALTH_TIMEOUT_S", "5")),
        restart_cooldown_s=float(os.getenv("SPECTER_RESTART_COOLDOWN_S", "60")),
        restart_timeout_s=float(os.getenv("SPECTER_RESTART_TIMEOUT_S", "30")),
        sqlite_path=Path(
            os.getenv("SPECTER_HEALTH_DB", r"C:\specter\Core\data\auto_healer.db")
        ),
        alerts_path=Path(
            os.getenv(
                "SPECTER_HEALTH_ALERTS",
                r"C:\specter\Core\exports\health_alerts.json",
            )
        ),
        log_path=Path(
            os.getenv("SPECTER_HEALTH_LOG", r"C:\specter\Core\logs\auto_healer.log")
        ),
    )
    endpoints = (
        EndpointConfig(
            name="gateway-8080",
            base_url=os.getenv("SPECTER_GATEWAY_8080", "http://127.0.0.1:8080"),
            priority=10,
            restart_command=_parse_restart_command("SPECTER_RESTART_CMD_8080"),
        ),
        EndpointConfig(
            name="gateway-18088",
            base_url=os.getenv("SPECTER_GATEWAY_18088", "http://127.0.0.1:18088"),
            priority=20,
            restart_command=_parse_restart_command("SPECTER_RESTART_CMD_18088"),
        ),
    )
    return AutoHealer(endpoints, config=config)


async def _main_async() -> None:
    healer = build_default_healer_from_env()
    try:
        await healer.run_forever()
    finally:
        await healer.close()


def main() -> int:
    """CLI entrypoint for sidecar execution."""

    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())