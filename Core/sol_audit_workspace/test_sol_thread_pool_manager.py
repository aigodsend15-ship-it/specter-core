# -*- coding: utf-8 -*-
import unittest
import tempfile
import shutil
from pathlib import Path

import sys
CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from sol_audit_workspace.sol_thread_pool_manager import SolThreadPoolManager

class SolThreadPoolManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="specter_pool_test_"))
        self.db_file = self.tmp_dir / "pool_fabric.sqlite3"
        self.reg_file = self.tmp_dir / "test_threads.json"
        self.pool = SolThreadPoolManager(db_path=self.db_file, max_threads=2, registry_file=self.reg_file)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_partition_and_scheduling(self):
        subtasks = [
            {"title": "Mapear AST", "instruction": "Inspecionar classes"},
            {"title": "Validar Fencing", "instruction": "Verificar lease_epoch"}
        ]
        ids = self.pool.partition_complex_goal("Auditar Core", subtasks)
        self.assertEqual(len(ids), 2)

        # Schedule 1
        res1 = self.pool.schedule_next_run()
        self.assertEqual(res1["status"], "DISPATCHED")
        self.assertEqual(res1["job_id"], ids[0])
        self.assertEqual(res1["thread_id"], "thread_sol_1")

        # Schedule 2
        res2 = self.pool.schedule_next_run()
        self.assertEqual(res2["status"], "DISPATCHED")
        self.assertEqual(res2["job_id"], ids[1])
        self.assertEqual(res2["thread_id"], "thread_sol_2")

        # Schedule 3 (Threads cheias)
        res3 = self.pool.schedule_next_run()
        self.assertEqual(res3["status"], "EMPTY_QUEUE")

if __name__ == "__main__":
    unittest.main()
