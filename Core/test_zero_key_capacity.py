from pathlib import Path
import tempfile
import unittest

from remote_worker_fabric import (
    ImmediateFailoverScheduler,
    LeaseStore,
    QuotaDomainLedger,
    RemoteWorker,
    RemoteWorkerRegistry,
)
from zero_key_capacity import (
    CapacityFailure,
    ZeroKeyCapacityRouter,
    classify_http_failure,
    zero_key_worker_eligible,
)


def worker(worker_id: str, quota: str, *, cost: float = 0.0, metadata=None) -> RemoteWorker:
    return RemoteWorker(
        worker_id=worker_id,
        provider="test",
        transport="test",
        endpoint_url="https://example.invalid",
        quota_domain=quota,
        capabilities=("reasoning", "code"),
        authorized=True,
        enabled=True,
        max_parallel=1,
        cost_per_hour_usd=cost,
        metadata=dict(metadata or {}),
    )


class ZeroKeyCapacityTests(unittest.TestCase):
    def test_402_classified_as_payment_required(self):
        self.assertEqual(classify_http_failure(402, ""), CapacityFailure.PAYMENT_REQUIRED)
        self.assertEqual(
            classify_http_failure(None, "402 Payment Required"),
            CapacityFailure.PAYMENT_REQUIRED,
        )

    def test_402_immediate_failover_without_sleep(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = RemoteWorkerRegistry(root / "workers.json")
            leases = LeaseStore(root / "leases.db")
            quota = QuotaDomainLedger(root / "quota.db")
            paid = worker("hf-jobs", "hf-jobs-paid")
            free = worker("free-worker", "free-independent")
            reg.upsert(paid)
            reg.upsert(free)
            scheduler = ImmediateFailoverScheduler(reg, leases, quota)
            router = ZeroKeyCapacityRouter(scheduler, quota)

            result = router.handle_failure(
                task_id="task-402",
                capability="reasoning",
                failed_worker=paid,
                status_code=402,
            )

            self.assertTrue(result.immediate_failover)
            self.assertFalse(result.retry_same_worker)
            self.assertEqual(result.failure, CapacityFailure.PAYMENT_REQUIRED)
            self.assertIsNotNone(result.replacement_lease)
            self.assertEqual(result.replacement_lease.worker_id, "free-worker")
            self.assertFalse(quota.routeable("hf-jobs-paid"))

    def test_same_quota_domain_is_not_selected_after_402(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = RemoteWorkerRegistry(root / "workers.json")
            leases = LeaseStore(root / "leases.db")
            quota = QuotaDomainLedger(root / "quota.db")
            a = worker("a", "shared")
            b = worker("b", "shared")
            c = worker("c", "other")
            for item in (a, b, c):
                reg.upsert(item)
            scheduler = ImmediateFailoverScheduler(reg, leases, quota)
            router = ZeroKeyCapacityRouter(scheduler, quota)
            result = router.handle_failure(
                task_id="task-shared",
                capability="reasoning",
                failed_worker=a,
                status_code=402,
            )
            self.assertIsNotNone(result.replacement_lease)
            self.assertEqual(result.replacement_lease.worker_id, "c")

    def test_no_replacement_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = RemoteWorkerRegistry(root / "workers.json")
            leases = LeaseStore(root / "leases.db")
            quota = QuotaDomainLedger(root / "quota.db")
            only = worker("only", "paid-domain")
            reg.upsert(only)
            scheduler = ImmediateFailoverScheduler(reg, leases, quota)
            router = ZeroKeyCapacityRouter(scheduler, quota)
            result = router.handle_failure(
                task_id="task-none",
                capability="reasoning",
                failed_worker=only,
                status_code=402,
            )
            self.assertIsNone(result.replacement_lease)

    def test_zero_key_worker_policy(self):
        self.assertTrue(zero_key_worker_eligible(worker("free", "q")))
        self.assertFalse(zero_key_worker_eligible(worker("paid", "q", cost=0.1)))
        self.assertFalse(
            zero_key_worker_eligible(
                worker("needs-key", "q", metadata={"requires_provider_api_key": True})
            )
        )
        self.assertFalse(
            zero_key_worker_eligible(
                worker("needs-paid", "q", metadata={"requires_paid_entitlement": True})
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
