# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER RIGHT-HAND V2 — UNIT TEST SUITE
================================================================================
Validação rigorosa com 100% de sucesso de:
1. FreeModelFallbackGateway:
   - Ordem estrita dos modelos Zen (6 tiers).
   - Cascata sob HTTP 429 RateLimit e registro no histórico.
   - Invariante de custo zero ($0.00 USD).
   - Cascata completa até fallback determinístico offline.
   - Integração com Matriz OSS do Specter.
2. SystemWatcher:
   - Patrulha de saúde, verificação de integridade do SQLite WAL.
   - Detecção de tarefas presas/expiradas (stuck tasks).
   - Conformidade contratual com WATCHER.md (:WATCH_STATUS, :ALERTS, etc.).
3. CoreBuilder:
   - Validação de sintaxe AST Python.
   - Aplicação atômica de patches com backup automático .bak.
   - Rejeição de código sintaticamente inválido.
   - Execução de testes unitários isolados via subprocess.
   - Conformidade com BUILDER.md (:BUILD_GOAL, :DESIGN, etc.).
4. RightHandCoordinator & RightHandOrchestrator:
   - Orquestração tri-agente ponta a ponta.
   - Formato contratual estrito de RIGHT_HAND.md (:GOAL, :STATUS, :PLAN, :ACTION, :REPORT, :MEM).
   - Persistência e recuperação no SQLite WAL (specter_fabric.sqlite3).
   - Determinação e consistência do hash de evidência criptográfica Milestone 1.
   - Despacho de tarefas de código para o Builder.
5. CLI:
   - Validação dos modos --patrol e --cycle via subprocess.
================================================================================
"""

import asyncio
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import right_hand_orchestrator as rho


class RightHandOrchestratorTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="specter_rhand_test_")
        self.db_path = Path(self.temp_dir) / "test_fabric.sqlite3"
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS broker_tasks (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    deadline REAL NOT NULL
                )
            """)
            con.execute("INSERT INTO broker_tasks VALUES ('task_ok', 'ATTAINED', 1, 3, ?)", (time.time() + 300,))
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Testes de Modelos Gratuitos e Invariante de Custo Zero
    # -------------------------------------------------------------------------

    def test_free_model_fallback_gateway_order(self):
        """Verifica se os 6 modelos prioritários do OpenCode Zen estão configurados na ordem exata."""
        gw = rho.FreeModelFallbackGateway()
        models = gw.model_list
        self.assertEqual(len(models), 6)
        self.assertEqual(models[0], "nemotron-3-ultra")
        self.assertEqual(models[1], "mimo-v2.5")
        self.assertEqual(models[2], "big-pickle")
        self.assertEqual(models[3], "ling-3.0-flash-fin")
        self.assertEqual(models[4], "muse-spark-1.2-contributor")
        self.assertEqual(models[5], "nemotron-3.5-lightning")
        self.assertEqual(gw.current_model, "nemotron-3-ultra")

    def test_free_model_gateway_cascade_on_simulated_error(self):
        """Simula 429 RateLimit nos dois primeiros modelos e valida cascata para o terceiro."""
        call_count = 0

        def failing_dispatch(model, msgs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError(f"HTTP 429 Rate Limit on model {model}")
            return f"Success response from {model}"

        gw = rho.FreeModelFallbackGateway(custom_dispatch_fn=failing_dispatch)

        async def run_cascade():
            return await gw.generate_response("Test prompt")

        resp = asyncio.run(run_cascade())
        self.assertTrue(resp.success)
        self.assertEqual(resp.model_used, "big-pickle")
        self.assertEqual(len(resp.hops), 3)
        self.assertEqual(resp.total_cost_usd, 0.0)
        self.assertIn("Success response", resp.content)
        self.assertTrue(len(resp.evidence_hash) > 0)
        self.assertEqual(len(gw.switch_history), 2)
        self.assertEqual(gw.switch_history[0]["from_model"], "nemotron-3-ultra")
        self.assertEqual(gw.switch_history[0]["to_model"], "mimo-v2.5")
        self.assertEqual(gw.switch_history[1]["from_model"], "mimo-v2.5")
        self.assertEqual(gw.switch_history[1]["to_model"], "big-pickle")

    def test_zero_cost_invariant(self):
        """Valida que o custo de inferência é estritamente $0.00 USD."""
        def free_fn(model, msgs):
            return "Zero cost reply"

        gw = rho.FreeModelFallbackGateway(custom_dispatch_fn=free_fn)
        resp = asyncio.run(gw.generate_response("Audit test"))
        self.assertEqual(resp.total_cost_usd, 0.0)
        for hop in resp.hops:
            self.assertEqual(hop.cost_usd, 0.0)

    def test_full_cascade_to_deterministic_fallback(self):
        """Valida fallback determinístico local quando todos os modelos remotos falham."""
        def all_fail(model, msgs):
            raise ConnectionError(f"Backend offline for {model}")

        gw = rho.FreeModelFallbackGateway(custom_dispatch_fn=all_fail)
        resp = asyncio.run(gw.generate_response("Emergency prompt"))
        self.assertTrue(resp.success)
        self.assertEqual(resp.model_used, "specter-local-deterministic-worker")
        self.assertEqual(resp.total_cost_usd, 0.0)
        self.assertIn("DETERMINISTIC_OFFLINE_FALLBACK", resp.content)
        self.assertEqual(len(resp.hops), len(rho.ZEN_FREE_MODELS))

    def test_oss_matrix_integration_fallback(self):
        """Testa o acionamento da Matriz OSS como fallback secundário gratuito."""
        class MockOSS:
            def __init__(self):
                self.invoked = False
            async def chat(self, prompt, messages=None):
                self.invoked = True
                class Res:
                    success = True
                    provider_id = "ollama_local"
                    output_text = "OSS matrix local completion"
                    evidence_hash = "mock_oss_hash_789"
                return Res()

        mock_oss = MockOSS()
        def fail_fn(m, msgs):
            raise RuntimeError("Zen rate limit")

        gw = rho.FreeModelFallbackGateway(custom_dispatch_fn=fail_fn, oss_matrix=mock_oss)
        resp = asyncio.run(gw.generate_response("Test with OSS Matrix"))
        self.assertTrue(resp.success)
        self.assertTrue(mock_oss.invoked)
        self.assertEqual(resp.model_used, "oss_matrix:ollama_local")
        self.assertEqual(resp.content, "OSS matrix local completion")

    # -------------------------------------------------------------------------
    # 2. Testes do SystemWatcher
    # -------------------------------------------------------------------------

    def test_system_watcher_patrol(self):
        """Valida patrulha de saúde do SystemWatcher e conformidade contratual."""
        watcher = rho.SystemWatcher(db_path=self.db_path)
        report = watcher.patrol()
        self.assertIsInstance(report, rho.WatcherReport)
        self.assertIn(report.watch_status, ["HEALTHY", "DEGRADED", "CRITICAL"])
        md = report.to_contract_markdown()
        self.assertIn(":WATCH_STATUS", md)
        self.assertIn(":ALERTS", md)
        self.assertIn(":SUGGESTED_ACTIONS", md)
        self.assertIn(":RAW_OBSERVATIONS", md)

    def test_system_watcher_stuck_tasks_detection(self):
        """Verifica que o Watcher identifica tarefas presas com deadline vencida."""
        with sqlite3.connect(str(self.db_path)) as con:
            # Tarefa em estado EXEC com deadline já expirada
            con.execute("INSERT INTO broker_tasks VALUES ('task_stuck', 'EXEC', 1, 3, ?)", (time.time() - 60,))
            con.commit()

        watcher = rho.SystemWatcher(db_path=self.db_path)
        stats = watcher.check_sqlite_wal()
        self.assertEqual(len(stats["stuck_tasks"]), 1)
        self.assertEqual(stats["stuck_tasks"][0]["id"], "task_stuck")

        rep = watcher.patrol()
        self.assertTrue(any("tarefas presas" in a for a in rep.alerts))

    def test_system_watcher_missing_db(self):
        """Verifica que ausência do banco aciona status CRITICAL no Watcher."""
        non_db = Path(self.temp_dir) / "non_existent.sqlite3"
        watcher = rho.SystemWatcher(db_path=non_db)
        rep = watcher.patrol()
        self.assertEqual(rep.watch_status, "CRITICAL")
        self.assertTrue(any("não encontrado" in a for a in rep.alerts))

    # -------------------------------------------------------------------------
    # 3. Testes do CoreBuilder
    # -------------------------------------------------------------------------

    def test_core_builder_ast_validation(self):
        """Valida checagem estática de sintaxe Python via ast.parse."""
        builder = rho.CoreBuilder()
        valid_code = "def hello(): return 'world'\n"
        invalid_code = "def hello( : return 'world'\n"
        ok, err = builder.validate_python_syntax(valid_code)
        self.assertTrue(ok)
        self.assertIsNone(err)

        ok_bad, err_bad = builder.validate_python_syntax(invalid_code)
        self.assertFalse(ok_bad)
        self.assertIsNotNone(err_bad)

    def test_core_builder_apply_patch_atomic_and_backup(self):
        """Valida gravação atômica de patch e criação de backup .bak."""
        builder = rho.CoreBuilder()
        target = Path(self.temp_dir) / "source_file.py"
        target.write_text("v = 1\n", encoding="utf-8")

        new_code = "v = 2\nw = 3\n"
        ok, msg = builder.apply_patch(target, new_code, create_backup=True)
        self.assertTrue(ok)
        self.assertEqual(target.read_text(encoding="utf-8"), new_code)

        # Confirma existência do arquivo de backup
        backups = list(Path(self.temp_dir).glob("source_file.py.bak_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "v = 1\n")

    def test_core_builder_apply_patch_rejects_corrupted_python(self):
        """Garante que código com erro de sintaxe não sobrescreve o arquivo original."""
        builder = rho.CoreBuilder()
        target = Path(self.temp_dir) / "protected.py"
        target.write_text("ACTIVE = True\n", encoding="utf-8")

        bad_code = "class Incomplete(\n"
        ok, msg = builder.apply_patch(target, bad_code)
        self.assertFalse(ok)
        self.assertIn("Sintaxe Python inválida", msg)
        self.assertEqual(target.read_text(encoding="utf-8"), "ACTIVE = True\n")

    def test_core_builder_run_tests(self):
        """Valida execução real de testes unitários isolados via subprocess."""
        builder = rho.CoreBuilder()
        test_file = Path(self.temp_dir) / "test_mini.py"
        test_file.write_text(
            "import unittest\n"
            "class MiniTest(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertEqual(2 * 2, 4)\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8"
        )
        res = builder.run_tests(test_file)
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)

    def test_core_builder_contract_format(self):
        """Valida formatação do relatório de entrega conforme BUILDER.md."""
        builder = rho.CoreBuilder()
        rep = asyncio.run(builder.execute_task(
            goal="Implementar validação de contratos",
            design_spec="Adição de asserts estruturais"
        ))
        self.assertIsInstance(rep, rho.BuilderReport)
        md = rep.to_contract_markdown()
        self.assertIn(":BUILD_GOAL", md)
        self.assertIn(":DESIGN", md)
        self.assertIn(":CODE_OR_CHANGES", md)
        self.assertIn(":TEST_PLAN", md)
        self.assertIn(":RISKS", md)
        self.assertIn(":NEXT", md)

    # -------------------------------------------------------------------------
    # 4. Testes do RightHandCoordinator & RightHandOrchestrator
    # -------------------------------------------------------------------------

    def test_right_hand_response_contract_markdown(self):
        """Valida formatação estrita do contrato RIGHT_HAND.md."""
        resp = rho.RightHandResponse(
            goal="Consolidar ecossistema Specter",
            status="OPERATIONAL",
            plan="Executar ciclo de teste e persistencia",
            action="Ativar RightHandCoordinator",
            report="Todos os modulos estao sincronizados.",
            mem={"test_key": "test_val"},
            evidence_hash="abcdef1234567890",
            model_used="nemotron-3-ultra"
        )
        md = resp.to_contract_markdown()
        self.assertIn(":GOAL", md)
        self.assertIn(":STATUS", md)
        self.assertIn(":PLAN", md)
        self.assertIn(":ACTION", md)
        self.assertIn(":REPORT", md)
        self.assertIn(":MEM", md)

    def test_right_hand_orchestration_cycle(self):
        """Valida execução completa de ciclo e persistência no SQLite WAL."""
        def deterministic_dispatch(model, msgs):
            return ":GOAL\nTest\n:STATUS\nOK\n:PLAN\nPlan\n:ACTION\nAct\n:REPORT\nDone\n:MEM\n{}"

        async def run_cycle():
            orchestrator = rho.RightHandOrchestrator(db_path=self.db_path)
            orchestrator.gateway.custom_dispatch_fn = deterministic_dispatch
            orchestrator.builder.gateway.custom_dispatch_fn = deterministic_dispatch
            res = await orchestrator.execute_cycle("Verificar prontidao operacional Specter")
            last_rep = orchestrator.get_last_report()
            return res, last_rep

        res, last_rep = asyncio.run(run_cycle())
        self.assertIsInstance(res, rho.RightHandResponse)
        self.assertEqual(res.goal, "Verificar prontidao operacional Specter")
        self.assertIsNotNone(last_rep)
        self.assertEqual(last_rep["goal"], "Verificar prontidao operacional Specter")
        self.assertTrue(len(res.evidence_hash) > 0)

    def test_tri_agent_builder_delegation(self):
        """Testa orquestração do Right-Hand delegando aplicação de patch ao Builder."""
        orchestrator = rho.RightHandOrchestrator(db_path=self.db_path)
        target_file = Path(self.temp_dir) / "patched_module.py"

        builder_task = {
            "goal": "Definir versão do protocolo V2",
            "design": "Atribuição de constante PROTOCOL_VERSION",
            "target_file": target_file,
            "code_content": "PROTOCOL_VERSION = '2.0.0'\n"
        }

        resp = asyncio.run(orchestrator.coordinator.orchestrate_goal(
            goal="Atualizar protocolo V2",
            builder_task=builder_task
        ))

        self.assertIn("Builder concluiu", resp.action)
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(encoding="utf-8"), "PROTOCOL_VERSION = '2.0.0'\n")

    def test_milestone1_cryptographic_evidence_hash(self):
        """Valida determinismo do hash de evidência Milestone 1 compatível."""
        payload = {"agent": "RightHand", "cost": 0.0, "status": "ATTAINED"}
        h1 = rho.compute_evidence_hash(payload)
        h2 = rho.compute_evidence_hash(payload)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    # -------------------------------------------------------------------------
    # 5. Testes de CLI
    # -------------------------------------------------------------------------

    def test_cli_execution_patrol_and_cycle(self):
        """Valida chamadas de linha de comando --patrol e --cycle via subprocess."""
        script_path = CORE_DIR / "right_hand_orchestrator.py"

        # 1. Testar --patrol
        proc_patrol = subprocess.run(
            [sys.executable, str(script_path), "--patrol"],
            capture_output=True,
            text=True,
            timeout=15
        )
        self.assertEqual(proc_patrol.returncode, 0)
        self.assertIn(":WATCH_STATUS", proc_patrol.stdout)

        # 2. Testar --cycle
        proc_cycle = subprocess.run(
            [sys.executable, str(script_path), "--cycle", "--goal", "Meta de Teste via CLI"],
            capture_output=True,
            text=True,
            timeout=15
        )
        self.assertEqual(proc_cycle.returncode, 0)
        self.assertIn(":GOAL", proc_cycle.stdout)
        self.assertIn("Meta de Teste via CLI", proc_cycle.stdout)
        self.assertIn(":REPORT", proc_cycle.stdout)


if __name__ == "__main__":
    unittest.main()
