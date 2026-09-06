# -*- coding: utf-8 -*-
import unittest
import tempfile
import shutil
import time
from pathlib import Path
from bridge_job_state import (
    BridgeJobManager,
    JobState,
    PayloadMismatchError,
    StaleLeaseEpochError,
    InvalidTransitionError
)


class BridgeJobStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix='specter_test_sol_bridge_'))
        self.db_file = self.tmp_dir / 'test_jobs.sqlite3'
        self.manager = BridgeJobManager(db_path=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_submit_and_idempotency_retry(self):
        payload = {'action': 'synthesize', 'target': 'module_a'}
        j1 = self.manager.submit_job('idem-job-1', payload, timeout_seconds=30.0)
        self.assertEqual(j1['state'], JobState.CREATED.value)
        self.assertEqual(j1['idempotency_key'], 'idem-job-1')

        # Retry com mesmo payload retorna o mesmo job
        j2 = self.manager.submit_job('idem-job-1', payload, timeout_seconds=30.0)
        self.assertEqual(j1['job_id'], j2['job_id'])
        self.assertEqual(j1['created_at'], j2['created_at'])

    def test_payload_conflict_same_key(self):
        p1 = {'action': 'synthesize', 'target': 'module_a'}
        p2 = {'action': 'synthesize', 'target': 'module_b'}
        self.manager.submit_job('idem-job-conflict', p1)
        with self.assertRaises(PayloadMismatchError):
            self.manager.submit_job('idem-job-conflict', p2)

    def test_two_interleaved_jobs_lifecycle(self):
        pA = {'job': 'A'}
        pB = {'job': 'B'}
        jA = self.manager.submit_job('key-A', pA)
        jB = self.manager.submit_job('key-B', pB)
        self.assertNotEqual(jA['job_id'], jB['job_id'])

        # Worker 1 claims A
        cA = self.manager.claim_job(jA['job_id'], 'worker-1')
        self.assertEqual(cA['state'], JobState.ACCEPTED.value)
        self.assertEqual(cA['lease_epoch'], 1)

        # Worker 2 claims B
        cB = self.manager.claim_job(jB['job_id'], 'worker-2')
        self.assertEqual(cB['state'], JobState.ACCEPTED.value)
        self.assertEqual(cB['lease_epoch'], 1)

        # Start running A
        rA = self.manager.start_running(jA['job_id'], lease_epoch=1)
        self.assertEqual(rA['state'], JobState.RUNNING.value)

        # Complete A with valid SHA-256 evidence
        hash_A = 'a' * 64
        compA = self.manager.complete_job(jA['job_id'], lease_epoch=1, result={'status': 'ok_A'}, evidence_hash=hash_A)
        self.assertEqual(compA['state'], JobState.SUCCEEDED.value)
        self.assertEqual(compA['evidence_hash'], hash_A)

        # Fail B
        fB = self.manager.fail_job(jB['job_id'], error_message='Execution failed', lease_epoch=1)
        self.assertEqual(fB['state'], JobState.FAILED.value)
        self.assertEqual(fB['error_message'], 'Execution failed')

    def test_stale_lease_epoch_rejection(self):
        j = self.manager.submit_job('key-stale', {'val': 123})
        c1 = self.manager.claim_job(j['job_id'], 'worker-1')
        epoch1 = c1['lease_epoch']

        # Worker 2 steals/re-claims
        c2 = self.manager.claim_job(j['job_id'], 'worker-2')
        epoch2 = c2['lease_epoch']
        self.assertGreater(epoch2, epoch1)

        # Worker 1 tenta transitar com epoch 1
        with self.assertRaises(StaleLeaseEpochError):
            self.manager.start_running(j['job_id'], lease_epoch=epoch1)

        # Worker 2 transita normalmente
        self.manager.start_running(j['job_id'], lease_epoch=epoch2)

        # Worker 1 tenta completar com epoch 1 obsoleto
        with self.assertRaises(StaleLeaseEpochError):
            self.manager.complete_job(j['job_id'], lease_epoch=epoch1, result={'out': 'late'}, evidence_hash='b'*64)

    def test_timeout_and_restart(self):
        j = self.manager.submit_job('key-timeout', {'val': 'fast'}, timeout_seconds=0.01)
        self.manager.claim_job(j['job_id'], 'worker-timeout')
        time.sleep(0.05)

        # Timeout sweep
        timed_out_count = self.manager.check_timeouts()
        self.assertGreaterEqual(timed_out_count, 1)

        status = self.manager.get_job(j['job_id'])
        self.assertEqual(status['state'], JobState.TIMED_OUT.value)

        # Novo manager sobre o mesmo banco (simulando reinicio)
        new_mgr = BridgeJobManager(db_path=self.db_file)
        restarted_status = new_mgr.get_job(j['job_id'])
        self.assertEqual(restarted_status['state'], JobState.TIMED_OUT.value)


if __name__ == '__main__':
    unittest.main()
