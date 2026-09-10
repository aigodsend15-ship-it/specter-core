from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from remote_worker_fabric import (
    ImmediateFailoverScheduler,
    Lease,
    QuotaDomainLedger,
    RemoteWorker,
)


class CapacityFailure(str, Enum):
    PAYMENT_REQUIRED = "payment_required"
    SATURATED = "saturated"
    AUTH_REQUIRED = "auth_required"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailoverResult:
    failure: CapacityFailure
    failed_worker_id: str
    failed_quota_domain: str
    immediate_failover: bool
    retry_same_worker: bool
    replacement_lease: Optional[Lease]
    detail: str


def classify_http_failure(status_code: Optional[int], text: str = "") -> CapacityFailure:
    value = (text or "").lower()
    if status_code == 402 or "payment required" in value:
        return CapacityFailure.PAYMENT_REQUIRED
    if status_code == 429:
        return CapacityFailure.SATURATED
    if any(
        marker in value
        for marker in (
            "quota reached",
            "quota exceeded",
            "rate limit",
            "usage limit",
            "resource exhausted",
            "capacity exhausted",
            "individual quota reached",
            "too many requests",
        )
    ):
        return CapacityFailure.SATURATED
    if status_code in (401, 403):
        return CapacityFailure.AUTH_REQUIRED
    if status_code is not None and 500 <= status_code <= 599:
        return CapacityFailure.TRANSIENT
    return CapacityFailure.UNKNOWN


class ZeroKeyCapacityRouter:
    """Immediate failover policy for operation with no provider API keys.

    A HTTP 402 is not retried or bypassed. The paid capacity is removed from the
    current routing set and another already-authorized, independent quota domain
    is selected immediately. There is deliberately no sleep/backoff in the task
    path. Provider authentication and billing controls are never circumvented.
    """

    PAYMENT_BLOCK_SECONDS = 10 * 365 * 24 * 3600

    def __init__(
        self,
        scheduler: ImmediateFailoverScheduler,
        quota_ledger: QuotaDomainLedger,
    ) -> None:
        self.scheduler = scheduler
        self.quota_ledger = quota_ledger

    def handle_failure(
        self,
        *,
        task_id: str,
        capability: str,
        failed_worker: RemoteWorker,
        status_code: Optional[int] = None,
        text: str = "",
        allow_paid: bool = False,
        max_cost_per_hour_usd: float = 0.0,
        ttl_seconds: float = 120.0,
    ) -> FailoverResult:
        failure = classify_http_failure(status_code, text)

        if failure == CapacityFailure.PAYMENT_REQUIRED:
            # 402 means the provider requires paid entitlement. Treat it as
            # unavailable in zero-key mode until explicitly re-enabled.
            self.quota_ledger.mark_saturated(
                failed_worker.quota_domain,
                "payment_required_402",
                probe_after_seconds=self.PAYMENT_BLOCK_SECONDS,
            )
            retry_same_worker = False
        elif failure == CapacityFailure.SATURATED:
            self.quota_ledger.mark_saturated(
                failed_worker.quota_domain,
                "quota_or_rate_limit",
                probe_after_seconds=900.0,
            )
            retry_same_worker = False
        elif failure == CapacityFailure.AUTH_REQUIRED:
            self.quota_ledger.mark_saturated(
                failed_worker.quota_domain,
                "auth_or_entitlement_required",
                probe_after_seconds=self.PAYMENT_BLOCK_SECONDS,
            )
            retry_same_worker = False
        elif failure == CapacityFailure.TRANSIENT:
            self.quota_ledger.mark_saturated(
                failed_worker.quota_domain,
                "transient_provider_failure",
                probe_after_seconds=30.0,
            )
            retry_same_worker = False
        else:
            # Unknown errors are isolated briefly, then verified later. The
            # current task still fails over immediately.
            self.quota_ledger.mark_saturated(
                failed_worker.quota_domain,
                "unknown_provider_failure",
                probe_after_seconds=30.0,
            )
            retry_same_worker = False

        replacement: Optional[Lease]
        try:
            replacement = self.scheduler.acquire_best(
                task_id,
                capability,
                allow_paid=allow_paid,
                max_cost_per_hour_usd=max_cost_per_hour_usd,
                ttl_seconds=ttl_seconds,
            )
        except RuntimeError:
            replacement = None

        return FailoverResult(
            failure=failure,
            failed_worker_id=failed_worker.worker_id,
            failed_quota_domain=failed_worker.quota_domain,
            immediate_failover=True,
            retry_same_worker=retry_same_worker,
            replacement_lease=replacement,
            detail=(
                "paid_capacity_disabled_zero_key_mode"
                if failure == CapacityFailure.PAYMENT_REQUIRED
                else "failed_domain_isolated"
            ),
        )


def zero_key_worker_eligible(worker: RemoteWorker) -> bool:
    """Return True only for workers explicitly declared usable without provider keys."""
    if not worker.authorized or not worker.enabled:
        return False
    if worker.cost_per_hour_usd > 0:
        return False
    metadata = worker.metadata or {}
    if metadata.get("requires_provider_api_key") is True:
        return False
    if metadata.get("requires_paid_entitlement") is True:
        return False
    return True
