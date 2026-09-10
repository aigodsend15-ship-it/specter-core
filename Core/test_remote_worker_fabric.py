from pathlib import Path
import tempfile
import unittest

from remote_worker_fabric import (
    ImmediateFailoverScheduler,
    LeaseStore,
    QuotaDomainLedger,
    RemoteWorker,
    RemoteWorkerRegistry,
    bootstrap_registry,
    classify_capacity_failure,
    worker_manifest_hash,
)


def make_worker(
    worker_id: str,
    quota: str,
    cost: float = 0.0,
    max_parallel: int = 1,
) -> RemoteWorker:
    return RemoteWorker(
        worker_id=worker_id,
        provider="test",
        transport="test",
        endpoint_url="https://example.invalid",
        quota_domain=quota,
        capabilities=("reasoning", "code"),
        authorized=True,
        max_parallel=max_parallel,
        cost_per_hour_usd=cost,
    )


class RemoteWorkerFabricTests(unittest.TestCase):
    def test_registry_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            reg = RemoteWorkerRegistry(Path(td) / "workers.json")
            reg.upsert(make_worker("a", "qa"))
            self.assertEqual(reg.get("a").worker_id, "a")
            self.assertEqual(len(reg.list()), 1)

    def test_shared_quota_domain_immediate_failover(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = RemoteWorkerRegistry(root / "workers.json")
            leases = LeaseStore(root / "leases.db")
            quota = QuotaDomainLedger(root / "quota.db")
            reg.upsert(make_worker("a", "shared"))
            reg.upsert(make_worker("b", "other"))
            quota.mark_saturated("shared", "429", probe_after_seconds=9999)
            sched = ImmediateFailoverScheduler(reg, leases, quota)
            lease = sched.acquire_best("task1", "reasoning")
            self.assertEqual(lease.worker_id, "b")

    def test_capacity_fencing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = make_worker("a", "q", max_parallel=1)
            leases = LeaseStore(root / "leases.db")
            first = leases.acquire(worker, "t1", ttl_seconds=60)
            with self.assertRaises(RuntimeError):
                leases.acquire(worker, "t2", ttl_seconds=60)
            self.assertTrue(leases.release(first.lease_id))
            second = leases.acquire(worker, "t2", ttl_seconds=60)
            self.assertGreater(second.lease_epoch, first.lease_epoch)

    def test_cost_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = RemoteWorkerRegistry(root / "workers.json")
            leases = LeaseStore(root / "leases.db")
            quota = QuotaDomainLedger(root / "quota.db")
            reg.upsert(make_worker("paid", "paidq", cost=1.0))
            sched = ImmediateFailoverScheduler(reg, leases, quota)
            self.assertEqual(sched.candidates("reasoning"), [])
            self.assertEqual(
                len(
                    sched.candidates(
                        "reasoning",
                        allow_paid=True,
                        max_cost_per_hour_usd=1.0,
                    )
                ),
                1,
            )

    def test_classifier(self):
        self.assertEqual(classify_capacity_failure("", 429), "saturated")
        self.assertEqual(
            classify_capacity_failure("Individual quota reached"),
            "saturated",
        )
        self.assertEqual(classify_capacity_failure("", 403), "auth_or_entitlement")
        self.assertEqual(classify_capacity_failure("", 503), "transient")

    def test_manifest_hash_is_stable(self):
        worker = make_worker("a", "q")
        self.assertEqual(worker_manifest_hash(worker), worker_manifest_hash(worker))
        self.assertEqual(len(worker_manifest_hash(worker)), 64)

    def test_bootstrap_hf_space(self):
        with tempfile.TemporaryDirectory() as td:
            reg = bootstrap_registry(Path(td) / "workers.json")
            row = reg.get("hf-hermes-bridge")
            self.assertEqual(row.provider, "huggingface")
            self.assertTrue(row.authorized)
            self.assertEqual(row.cost_per_hour_usd, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
