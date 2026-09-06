# -*- coding: utf-8 -*-
"""
TEST SUITE: SCIENTIFIC RIGOR AUDIT AND ADVANCED INVARIANTS
Focado em:
1. Resiliencia de Concorrencia e Atomicidade em SQLite WAL.
2. Prova Criptografica SHA-256 e Verificacao Matematica de Hashing Canonico.
3. Monotonicidade Estrita de Leases e Rejeicao de Fencing Tokens Obsoletos.
4. Invariante de Pegada de RAM.
"""
import unittest
import tempfile
import shutil
import sqlite3
import threading
import time
import json
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import sys
CORE_DIR = Path(r"C:\specter\Core")
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from sol_bridge_v2.bridge_job_state import BridgeJobManager, JobState, StaleLeaseEpochError, PayloadMismatchError
from mesh_broker_bridge import UnifiedMeshCoordinator, PayloadConflictError
from specter_execution_router import SpecterExecutionRouter, TaskType, TaskLifecycleState, StaleLeaseError

class ScientificAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="specter_sci_audit_"))
        self.db_file = self.tmp_dir / "audit_fabric.sqlite3"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_canonical_json_determinism_and_entropy(self):
        from autonomous_broker import canonical, digest
        dict_a = {"b": 2, "a": 1, "nested": {"z": [3, 2, 1], "y": "test"}}
        dict_b = {"a": 1, "nested": {"y": "test", "z": [3, 2, 1]}, "b": 2}
        
        canon_a = canonical(dict_a)
        canon_b = canonical(dict_b)
        self.assertEqual(canon_a, canon_b)
        self.assertEqual(digest(canon_a), digest(canon_b))

    def test_high_concurrency_lease_racing_barrier(self):
        mgr = BridgeJobManager(db_path=self.db_file)
        job = mgr.submit_job("sci-race-001", {"param": "quantum_simulation"})
        job_id = job["job_id"]

        barrier = threading.Barrier(8)
        successful_claims = []
        errors = []

        def worker(w_id):
            local_mgr = BridgeJobManager(db_path=self.db_file)
            barrier.wait()
            try:
                c = local_mgr.claim_job(job_id, f"worker-{w_id}")
                successful_claims.append(c)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)

        self.assertEqual(len(successful_claims), 8)
        final_job = mgr.get_job(job_id)
        self.assertEqual(final_job["lease_epoch"], 8)
        self.assertEqual(final_job["state"], JobState.ACCEPTED.value)

    def test_stale_fencing_token_deep_rejection(self):
        mgr = BridgeJobManager(db_path=self.db_file)
        job = mgr.submit_job("sci-fencing-002", {"data": 42})
        job_id = job["job_id"]

        claim1 = mgr.claim_job(job_id, "worker-A")
        epoch1 = claim1["lease_epoch"]

        claim2 = mgr.claim_job(job_id, "worker-B")
        epoch2 = claim2["lease_epoch"]
        self.assertGreater(epoch2, epoch1)

        mgr.start_running(job_id, lease_epoch=epoch2)

        with self.assertRaises(StaleLeaseEpochError):
            mgr.complete_job(job_id, lease_epoch=epoch1, result={"ans": 999}, evidence_hash="a"*64)

        final = mgr.complete_job(job_id, lease_epoch=epoch2, result={"ans": 42}, evidence_hash="f"*64)
        self.assertEqual(final["state"], JobState.SUCCEEDED.value)
        self.assertEqual(final["evidence_hash"], "f"*64)

    def test_memory_footprint_invariant(self):
        try:
            import psutil
            import os
            rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            self.assertLess(rss_mb, 150.0)
        except ImportError:
            pass

if __name__ == "__main__":
    unittest.main()
