# -*- coding: utf-8 -*-
import unittest
import tempfile
import shutil
import sqlite3
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from mesh_broker_bridge import UnifiedMeshCoordinator, PayloadConflictError


class MeshBrokerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix='specter_test_bridge_'))
        self.db_file = self.tmp_dir / 'test_bridge.sqlite3'
        self.coord = UnifiedMeshCoordinator(db_path=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_submit_goal_idempotency(self):
        res1 = self.coord.submit_goal('Meta de teste de idempotencia', 'idem-1001')
        self.assertEqual(res1['status'], 'QUEUED')
        self.assertIsNotNone(res1['task_id'])

        res2 = self.coord.submit_goal('Meta de teste de idempotencia', 'idem-1001')
        self.assertEqual(res1['task_id'], res2['task_id'])
        self.assertEqual(res1['semantic_memory_id'], res2['semantic_memory_id'])

        # Verify event sourcing OCC log has exactly 1 event for aggregate
        events = self.coord.checkpoint_engine.get_aggregate_events(res1['task_id'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].expected_version, 1)
        self.assertEqual(events[0].event_type, 'goal.created')

        # Verify outbox status is DISPATCHED
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM mesh_outbox WHERE idempotency_key = ?", ('idem-1001',)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'DISPATCHED')
            self.assertEqual(row['aggregate_id'], res1['task_id'])

    def test_submit_goal_different_keys(self):
        res1 = self.coord.submit_goal('Meta A', 'idem-A')
        res2 = self.coord.submit_goal('Meta B', 'idem-B')
        self.assertNotEqual(res1['task_id'], res2['task_id'])

        events_a = self.coord.checkpoint_engine.get_aggregate_events(res1['task_id'])
        events_b = self.coord.checkpoint_engine.get_aggregate_events(res2['task_id'])
        self.assertEqual(len(events_a), 1)
        self.assertEqual(len(events_b), 1)

    def test_concurrent_submissions_same_key_parallel_threading(self):
        """
        Cobre: Duas submissões simultâneas com a mesma chave em paralelo (threading).
        Ambas devem concorrer de forma segura via BEGIN IMMEDIATE e retornar o mesmo task_id,
        com exatamente 1 tarefa no broker e 1 evento no log.
        """
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker(thread_idx):
            coord = UnifiedMeshCoordinator(db_path=self.db_file)
            barrier.wait()
            try:
                res = coord.submit_goal('Meta Paralela Concorrente', 'idem-parallel-1')
                results.append((thread_idx, res))
            except Exception as e:
                errors.append((thread_idx, e))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Erros inesperados nas threads: {errors}")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][1]['task_id'], results[1][1]['task_id'])
        self.assertEqual(results[0][1]['semantic_memory_id'], results[1][1]['semantic_memory_id'])

        # Verifica unicidade no banco de dados
        task_id = results[0][1]['task_id']
        events = self.coord.checkpoint_engine.get_aggregate_events(task_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].expected_version, 1)

        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            tasks = conn.execute("SELECT * FROM broker_tasks WHERE idem = 'idem-parallel-1'").fetchall()
            self.assertEqual(len(tasks), 1)
            outbox = conn.execute("SELECT * FROM mesh_outbox WHERE idempotency_key = 'idem-parallel-1'").fetchall()
            self.assertEqual(len(outbox), 1)
            self.assertEqual(outbox[0]['status'], 'DISPATCHED')

    def test_payload_conflict_same_key(self):
        """
        Cobre: Tentativa de submissão da mesma chave com texto/especificação diferente.
        Deve lançar PayloadConflictError (subclasse de ValueError).
        """
        self.coord.submit_goal('Texto Original da Meta', 'idem-conflict-key')

        # Tentativa com texto diferente sob a mesma chave
        with self.assertRaises(PayloadConflictError) as ctx:
            self.coord.submit_goal('Texto Diferente Alterado', 'idem-conflict-key')

        self.assertIn("reused with different specification", str(ctx.exception))

    def test_simulated_crash_and_outbox_reconciliation(self):
        """
        Cobre: Queda simulada entre a inserção da tarefa e a emissão do evento/outbox.
        O processo de reconciliação (reconcile_outbox / run_cycle) recupera e publica o evento.
        """
        # 1. Simula crash antes do dispatch do outbox
        with self.assertRaises(RuntimeError) as ctx:
            self.coord.submit_goal(
                'Meta Antes da Queda',
                'idem-crash-test',
                _simulate_crash_before_outbox_dispatch=True
            )
        self.assertIn("Simulated crash", str(ctx.exception))

        # 2. Confirma estado pós-queda:
        # A tarefa foi commitada atomicamente com o outbox (status PENDING),
        # mas o evento ainda NÃO foi emitido no event_log (devido ao crash simulado)
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            task_row = conn.execute("SELECT * FROM broker_tasks WHERE idem = 'idem-crash-test'").fetchone()
            self.assertIsNotNone(task_row)
            task_id = task_row['id']

            outbox_row = conn.execute("SELECT * FROM mesh_outbox WHERE idempotency_key = 'idem-crash-test'").fetchone()
            self.assertIsNotNone(outbox_row)
            self.assertEqual(outbox_row['status'], 'PENDING')

        # Confirma que o event_log ainda está vazio para este aggregate
        events_before = self.coord.checkpoint_engine.get_aggregate_events(task_id)
        self.assertEqual(len(events_before), 0)

        # 3. Processo de recuperação/reconciliação pós-queda
        recovered_coord = UnifiedMeshCoordinator(db_path=self.db_file)
        reconciled_count = recovered_coord.reconcile_outbox()
        self.assertEqual(reconciled_count, 1)

        # 4. Verifica que o evento agora está publicado no event_log com versão 1
        events_after = recovered_coord.checkpoint_engine.get_aggregate_events(task_id)
        self.assertEqual(len(events_after), 1)
        self.assertEqual(events_after[0].expected_version, 1)
        self.assertEqual(events_after[0].event_type, 'goal.created')
        self.assertEqual(events_after[0].payload['goal'], 'Meta Antes da Queda')

        # 5. Verifica que o outbox foi atualizado para DISPATCHED
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            outbox_after = conn.execute("SELECT * FROM mesh_outbox WHERE idempotency_key = 'idem-crash-test'").fetchone()
            self.assertEqual(outbox_after['status'], 'DISPATCHED')
            self.assertIsNotNone(outbox_after['dispatched_at'])

        # 6. Reconciliação repetida não re-publica (idempotente)
        second_reconcile = recovered_coord.reconcile_outbox()
        self.assertEqual(second_reconcile, 0)
        events_final = recovered_coord.checkpoint_engine.get_aggregate_events(task_id)
        self.assertEqual(len(events_final), 1)


if __name__ == '__main__':
    unittest.main()
