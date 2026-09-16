from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from auto_healer import (
    AlertStore,
    AutoHealer,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    EndpointConfig,
    FailureKind,
    HealthRepository,
    HealthResult,
    HealerConfig,
    RestartResult,
    configure_structured_logging,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeProbe:
    def __init__(self, results: dict[str, list[HealthResult]]) -> None:
        self.results = {key: deque(value) for key, value in results.items()}
        self.started = False
        self.closed = False
        self.calls: list[str] = []

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def check(self, endpoint: EndpointConfig, timeout_s: float) -> HealthResult:
        self.calls.append(endpoint.name)
        queue = self.results[endpoint.name]
        if len(queue) > 1:
            return queue.popleft()
        return queue[0]


class FakeRestarter:
    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[str] = []

    async def restart(self, endpoint: EndpointConfig, timeout_s: float) -> RestartResult:
        self.calls.append(endpoint.name)
        return RestartResult(success=self.succeed, returncode=0 if self.succeed else 1)


def ok(status: int = 200) -> HealthResult:
    return HealthResult(healthy=True, latency_ms=1.0, status_code=status)


def timeout() -> HealthResult:
    return HealthResult(
        healthy=False,
        latency_ms=5000.0,
        error="timed out",
        failure_kind=FailureKind.TIMEOUT,
    )


def server_error() -> HealthResult:
    return HealthResult(
        healthy=False,
        latency_ms=3.0,
        status_code=503,
        error="unhealthy HTTP status 503",
        failure_kind=FailureKind.HTTP_STATUS,
    )


def make_config(tmp_path: Path, *, breaker: CircuitBreakerConfig | None = None) -> HealerConfig:
    return HealerConfig(
        health_interval_s=30,
        request_timeout_s=5,
        restart_cooldown_s=60,
        restart_timeout_s=5,
        sqlite_path=tmp_path / "health.db",
        alerts_path=tmp_path / "health_alerts.json",
        log_path=tmp_path / "auto_healer.log",
        circuit_breaker=breaker or CircuitBreakerConfig(),
    )


def test_circuit_breaker_closed_open_half_open_closed() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_s=10,
            half_open_success_threshold=2,
        ),
        clock=clock,
    )

    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert not breaker.allow_request()

    clock.advance(10)
    assert breaker.allow_request()
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_half_open_failure_reopens_immediately() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=5),
        clock=clock,
    )
    breaker.record_failure()
    clock.advance(5)
    assert breaker.allow_request()
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN


def test_sqlite_wal_and_cumulative_metrics(tmp_path: Path) -> None:
    repo = HealthRepository(tmp_path / "metrics.db")
    assert repo.journal_mode() == "wal"

    repo.record_health(
        endpoint="gateway-8080",
        healthy=True,
        elapsed_s=30,
        state=CircuitState.CLOSED,
        status_code=200,
        consecutive_failures=0,
        error=None,
    )
    repo.record_health(
        endpoint="gateway-8080",
        healthy=False,
        elapsed_s=10,
        state=CircuitState.CLOSED,
        status_code=None,
        consecutive_failures=1,
        error="timeout",
    )
    repo.record_restart("gateway-8080")

    row = repo.snapshot()["gateway-8080"]
    assert row["uptime_seconds"] == 30
    assert row["downtime_seconds"] == 10
    assert row["restart_count"] == 1
    assert row["last_error"] == "timeout"


def test_primary_failure_routes_to_secondary_and_restarts(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        primary = EndpointConfig("gateway-8080", "http://127.0.0.1:8080", priority=10)
        secondary = EndpointConfig("gateway-18088", "http://127.0.0.1:18088", priority=20)
        probe = FakeProbe({primary.name: [timeout()], secondary.name: [ok()]})
        restarter = FakeRestarter()
        config = make_config(tmp_path)
        healer = AutoHealer(
            [primary, secondary],
            config=config,
            probe=probe,
            restart_executor=restarter,
            repository=HealthRepository(config.sqlite_path),
            alert_store=AlertStore(config.alerts_path),
            clock=clock,
        )
        await probe.start()
        await healer.run_once()

        assert healer.active_endpoint == secondary
        assert healer.resolve_target("/v1/models") == "http://127.0.0.1:18088/v1/models"
        assert restarter.calls == [primary.name]

        metrics = await healer.metrics_snapshot()
        assert metrics[primary.name]["restart_count"] == 1

        payload = json.loads(config.alerts_path.read_text(encoding="utf-8"))
        assert payload["active_endpoint"] == secondary.name
        assert any(item["type"] == "ROUTE_CHANGED" for item in payload["alerts"])
        assert any(item["type"] == "RESTART_SUCCEEDED" for item in payload["alerts"])

        await healer.close()

    asyncio.run(scenario())


def test_primary_failback_after_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        primary = EndpointConfig("gateway-8080", "http://127.0.0.1:8080", priority=10)
        secondary = EndpointConfig("gateway-18088", "http://127.0.0.1:18088", priority=20)
        probe = FakeProbe(
            {
                primary.name: [timeout(), ok()],
                secondary.name: [ok(), ok()],
            }
        )
        config = make_config(tmp_path)
        healer = AutoHealer(
            [primary, secondary],
            config=config,
            probe=probe,
            restart_executor=FakeRestarter(),
            repository=HealthRepository(config.sqlite_path),
            alert_store=AlertStore(config.alerts_path),
            clock=clock,
        )
        await probe.start()

        await healer.run_once()
        assert healer.active_endpoint == secondary

        clock.advance(30)
        await healer.run_once()
        assert healer.active_endpoint == primary

        await healer.close()

    asyncio.run(scenario())


def test_restart_cooldown_prevents_restart_storm(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        endpoint = EndpointConfig("gateway-8080", "http://127.0.0.1:8080", priority=10)
        probe = FakeProbe({endpoint.name: [timeout(), timeout(), timeout()]})
        restarter = FakeRestarter()
        config = make_config(
            tmp_path,
            breaker=CircuitBreakerConfig(failure_threshold=99, recovery_timeout_s=60),
        )
        healer = AutoHealer(
            [endpoint],
            config=config,
            probe=probe,
            restart_executor=restarter,
            repository=HealthRepository(config.sqlite_path),
            alert_store=AlertStore(config.alerts_path),
            clock=clock,
        )
        await probe.start()

        await healer.run_once()
        clock.advance(30)
        await healer.run_once()
        assert restarter.calls == [endpoint.name]

        clock.advance(30)
        await healer.run_once()
        assert restarter.calls == [endpoint.name, endpoint.name]
        await healer.close()

    asyncio.run(scenario())


def test_open_circuit_skips_probe_until_recovery_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        endpoint = EndpointConfig("gateway-8080", "http://127.0.0.1:8080")
        probe = FakeProbe({endpoint.name: [server_error(), ok()]})
        config = make_config(
            tmp_path,
            breaker=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=60),
        )
        healer = AutoHealer(
            [endpoint],
            config=config,
            probe=probe,
            restart_executor=FakeRestarter(),
            repository=HealthRepository(config.sqlite_path),
            alert_store=AlertStore(config.alerts_path),
            clock=clock,
        )
        await probe.start()

        await healer.run_once()
        assert probe.calls == [endpoint.name]
        clock.advance(30)
        await healer.run_once()
        assert probe.calls == [endpoint.name]

        clock.advance(30)
        await healer.run_once()
        assert probe.calls == [endpoint.name, endpoint.name]
        assert healer.active_endpoint == endpoint
        await healer.close()

    asyncio.run(scenario())


def test_open_circuit_preserves_root_last_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        endpoint = EndpointConfig("gateway-8080", "http://127.0.0.1:8080")
        probe = FakeProbe({endpoint.name: [timeout()]})
        config = make_config(
            tmp_path,
            breaker=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=60),
        )
        healer = AutoHealer(
            [endpoint],
            config=config,
            probe=probe,
            restart_executor=FakeRestarter(),
            repository=HealthRepository(config.sqlite_path),
            alert_store=AlertStore(config.alerts_path),
            clock=clock,
        )
        await probe.start()
        await healer.run_once()
        clock.advance(30)
        await healer.run_once()  # breaker OPEN: no network probe

        metrics = await healer.metrics_snapshot()
        assert metrics[endpoint.name]["last_error"] == "timed out"
        await healer.close()

    asyncio.run(scenario())


def test_start_performs_single_immediate_probe(tmp_path: Path) -> None:
    async def scenario() -> None:
        endpoint = EndpointConfig("gateway-8080", "http://127.0.0.1:8080")
        probe = FakeProbe({endpoint.name: [ok()]})
        config = HealerConfig(
            health_interval_s=3600,
            request_timeout_s=5,
            sqlite_path=tmp_path / "health.db",
            alerts_path=tmp_path / "alerts.json",
            log_path=tmp_path / "healer.log",
        )
        healer = AutoHealer(
            [endpoint],
            config=config,
            probe=probe,
            restart_executor=FakeRestarter(),
        )
        await healer.start()
        await asyncio.sleep(0)
        assert probe.calls == [endpoint.name]
        await healer.close()

    asyncio.run(scenario())


def test_structured_logging_rotates_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "rotating.log"
    logger = configure_structured_logging(path, max_bytes=300, backup_count=2)
    for index in range(20):
        logger.info(
            "rotation-payload-" + ("x" * 120),
            extra={"event": "rotation_test", "sequence": index},
        )
    for handler in logger.handlers:
        handler.flush()

    assert path.exists()
    assert Path(str(path) + ".1").exists()
    latest = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert latest["event"] == "rotation_test"
    assert latest["level"] == "INFO"