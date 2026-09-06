# -*- coding: utf-8 -*-
"""
================================================================================
TEST SUITE: SPECTER EXECUTION ROUTER & PROVIDER DISPATCH ENGINE
Validação estrita em bancos de dados temporários de:
1. Ciclo de vida: submit -> claim -> execute -> verify -> durable result.
2. Preservação estrita de artifact.write_utf8.v1 via Local Worker.
3. Roteamento tipado para matriz de provedores (Codex, OSS, Local).
4. Fencing token / lease_epoch contra split-brain e sobrescrita por leases antigos.
5. Rejeição imediata de contratos inválidos e divergências de hash criptográfico.
================================================================================
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

CORE_DIR = Path(r"C:\specter\Core")
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from specter_execution_router import (
    SpecterExecutionRouter,
    TaskType,
    TaskLifecycleState,
    StaleLeaseError,
    ContractVerificationError
)
from autonomous_broker import digest, canonical
from oss_provider_matrix import (
    OSSProviderMatrix,
    BaseOSSProvider,
    Capability,
    ExecutionRequest,
    ExecutionResponse,
    ProviderStatus,
    ProviderType,
    LocalDeterministicProvider
)


class MockOSSModelProvider(BaseOSSProvider):
    """Provedor mock para testar roteamento para modelos sem chamar GPU/rede externa."""
    def __init__(self, provider_id: str = "mock_codex", priority: int = 15):
        super().__init__(
            provider_id=provider_id,
            provider_type=ProviderType.GENERIC_OSS,
            endpoint="mock://codex",
            capabilities={Capability.CHAT.value, Capability.CODE_EXEC.value, Capability.GENERATE.value},
            priority=priority
        )
        self.call_count = 0

    async def check_health(self):
        return None

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        self.call_count += 1
        output = f"SYNTHESIZED_CODE_BY_{self.provider_id.upper()}: {request.prompt or request.code}"
        return ExecutionResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            provider_type=self.provider_type.value,
            success=True,
            output_text=output,
            execution_result={"tokens": 42, "code": request.code},
            latency_ms=12.5
        )


class SpecterExecutionRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="specter_router_test_"))
        self.db_file = self.tmp_dir / "fabric_test.sqlite3"

        # Criar matriz customizada com mock codex + local worker
        self.matrix = OSSProviderMatrix(db_path=self.db_file)
        self.mock_codex = MockOSSModelProvider("codex_desktop", priority=15)
        self.local_worker = LocalDeterministicProvider("local_worker", priority=100)
        self.matrix.register_provider(self.mock_codex)
        self.matrix.register_provider(self.local_worker)

        self.router = SpecterExecutionRouter(db_path=self.db_file, matrix=self.matrix)

    async def asyncTearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Teste 1: artifact.write_utf8.v1 preservado e executado pelo Local Worker
    # -------------------------------------------------------------------------
    async def test_artifact_write_utf8_full_lifecycle(self):
        content = "Texto de teste do Specter para persistencia deterministica"
        task_id = self.router.submit_task(
            task_type=TaskType.ARTIFACT_WRITE.value,
            payload={"text": content},
            idem="idem-write-utf8-001"
        )
        self.assertIsNotNone(task_id)

        # Claim com fencing token
        claim = self.router.claim_task(worker_id="worker_local_1", task_id=task_id)
        self.assertEqual(claim["fencing_token"], 1)
        self.assertEqual(claim["state"], TaskLifecycleState.CLAIMED.value)

        # Execução
        exec_out = await self.router.execute_task(claim)
        self.assertTrue(exec_out["success"])
        self.assertEqual(exec_out["provider_id"], "local_worker")
        self.assertTrue(Path(exec_out["artifact_path"]).is_file())

        expected_hash = digest(content.encode("utf-8"))
        self.assertEqual(exec_out["artifact_sha256"], expected_hash)

        # Commit durável
        final_task = self.router.commit_result(task_id, claim["fencing_token"], exec_out)
        self.assertEqual(final_task["state"], TaskLifecycleState.ATTAINED.value)
        self.assertEqual(final_task["resolved_provider"], "local_worker")
        self.assertEqual(final_task["output_hash"], expected_hash)
        self.assertIsNotNone(final_task["evidence_hash"])

        # Verificar se a evidência foi registrada com fencing_token
        with self.router.connection() as con:
            ev = con.execute("SELECT * FROM routed_task_evidence WHERE task_id = ?", (task_id,)).fetchone()
            self.assertIsNotNone(ev)
            self.assertEqual(ev["lease_epoch"], 1)
            manifest = json.loads(ev["manifest"])
            self.assertEqual(manifest["verdict"], "PASS")
            self.assertEqual(manifest["artifact_sha256"], expected_hash)

    # -------------------------------------------------------------------------
    # Teste 2: Roteamento de geração de código para Codex Desktop / OSS
    # -------------------------------------------------------------------------
    async def test_code_generation_routed_to_codex(self):
        task_id = self.router.submit_task(
            task_type=TaskType.CODE_GEN.value,
            payload={"prompt": "def calculate_matrix(): pass", "code": "pass"},
            idem="idem-codegen-001"
        )

        claim = self.router.claim_task(worker_id="worker_codex_1", task_id=task_id)
        self.assertEqual(claim["fencing_token"], 1)

        exec_out = await self.router.execute_task(claim)
        self.assertTrue(exec_out["success"])
        self.assertEqual(exec_out["provider_id"], "codex_desktop")
        self.assertIn("SYNTHESIZED_CODE_BY_CODEX_DESKTOP", exec_out["output_payload"])

        final_task = self.router.commit_result(task_id, claim["fencing_token"], exec_out)
        self.assertEqual(final_task["state"], TaskLifecycleState.ATTAINED.value)
        self.assertEqual(final_task["resolved_provider"], "codex_desktop")

    # -------------------------------------------------------------------------
    # Teste 3: Proteção de Fencing Token contra Leases Antigos (Split-Brain)
    # -------------------------------------------------------------------------
    async def test_stale_lease_epoch_fencing_rejection(self):
        task_id = self.router.submit_task(
            task_type=TaskType.ARTIFACT_WRITE.value,
            payload={"text": "Protecao contra lease obsoleto"},
            idem="idem-fencing-001"
        )

        # Worker 1 reivindica lease com lease_duration pequeno
        claim_worker1 = self.router.claim_task(worker_id="worker_slow_1", task_id=task_id, lease_duration=0.1)
        self.assertEqual(claim_worker1["fencing_token"], 1)

        # Simular timeout do lease expirando
        time.sleep(0.15)

        # Worker 2 assume a tarefa devido à expiração do lease do Worker 1
        claim_worker2 = self.router.claim_task(worker_id="worker_fast_2", task_id=task_id, lease_duration=30.0)
        self.assertEqual(claim_worker2["fencing_token"], 2)

        # Worker 2 completa e comita com sucesso
        exec_w2 = await self.router.execute_task(claim_worker2)
        final_w2 = self.router.commit_result(task_id, claim_worker2["fencing_token"], exec_w2)
        self.assertEqual(final_w2["state"], TaskLifecycleState.ATTAINED.value)
        self.assertEqual(final_w2["lease_epoch"], 2)

        # Worker 1 retardado tenta executar ou comitar com o fencing token antigo (epoch 1)
        fake_stale_exec = {
            "provider_id": "worker_slow_1",
            "output_payload": "Tentativa zumbi de sobrescrever",
            "artifact_sha256": digest(b"corrompido")
        }
        with self.assertRaises(StaleLeaseError):
            self.router.commit_result(task_id, fencing_token=1, execution_output=fake_stale_exec)

        # Garantir que o resultado legítimo do Worker 2 não foi sobrescrito
        current = self.router.get_task(task_id)
        self.assertEqual(current["lease_epoch"], 2)
        self.assertEqual(current["state"], TaskLifecycleState.ATTAINED.value)

    # -------------------------------------------------------------------------
    # Teste 4: Falha em verificação criptográfica rejeita ATTAINED
    # -------------------------------------------------------------------------
    async def test_cryptographic_verification_mismatch_rejected(self):
        task_id = self.router.submit_task(
            task_type=TaskType.ARTIFACT_WRITE.value,
            payload={"text": "Texto integro original"},
            idem="idem-verify-tamper-001"
        )
        claim = self.router.claim_task(worker_id="worker_tamper", task_id=task_id)

        corrupted_output = {
            "provider_id": "local_worker",
            "output_payload": "Texto adulterado",
            "artifact_sha256": digest(b"Texto adulterado divergente")
        }

        with self.assertRaises(ContractVerificationError):
            self.router.commit_result(task_id, claim["fencing_token"], corrupted_output)

        # A tarefa não deve estar ATTAINED
        task_curr = self.router.get_task(task_id)
        self.assertNotEqual(task_curr["state"], TaskLifecycleState.ATTAINED.value)

    # -------------------------------------------------------------------------
    # Teste 5: Ciclo completo automatizado via run_cycle
    # -------------------------------------------------------------------------
    async def test_run_cycle_drains_mixed_tasks(self):
        # 1. Tarefa de persistencia (Local Worker)
        t1 = self.router.submit_task(
            task_type=TaskType.ARTIFACT_WRITE.value,
            payload={"text": "Persistencia Ciclo 1"},
            idem="cycle-t1"
        )
        # 2. Tarefa de codigo (Codex Matrix)
        t2 = self.router.submit_task(
            task_type=TaskType.CODE_GEN.value,
            payload={"prompt": "Gere uma funcao fibonacci"},
            idem="cycle-t2"
        )

        res = await self.router.run_cycle()
        self.assertEqual(res["processed_count"], 2)

        task1 = self.router.get_task(t1)
        task2 = self.router.get_task(t2)

        self.assertEqual(task1["state"], TaskLifecycleState.ATTAINED.value)
        self.assertEqual(task1["resolved_provider"], "local_worker")

        self.assertEqual(task2["state"], TaskLifecycleState.ATTAINED.value)
        self.assertEqual(task2["resolved_provider"], "codex_desktop")


if __name__ == "__main__":
    unittest.main(verbosity=2)
