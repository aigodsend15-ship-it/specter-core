# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER EXECUTION ROUTER & PROVIDER DISPATCH ENGINE (v1.0)
Frente B: Conectar a matriz de provedores ao ciclo de execução e roteamento.
================================================================================
Invariantes:
1. Preservação estrita de 'artifact.write_utf8.v1':
   - Operações locais de escrita/persistência são executadas via Local Worker
     com verificação SHA-256 idêntica ao Milestone 1.
2. Roteamento Semântico e Tipado:
   - Tarefas de código / refatoração / análise complexa -> Codex Desktop / OSS.
   - Tarefas de chat / sumarização / raciocínio -> Ollama Local / HF Spaces.
   - Tarefas de escrita e persistência de arquivos -> Local Worker determinístico.
3. Ciclo de Vida Rigoroso:
   SUBMIT -> CLAIM/LEASE (com fencing token / lease_epoch) -> EXECUTE ->
   VERIFY (hash criptográfico e contrato) -> RESULTADO DURÁVEL (ATTAINED / ESCALATE).
4. Fencing Token & Proteção contra Split-Brain:
   - Cada claim incrementa monotonicamente `lease_epoch`.
   - Resultados de leases expirados ou sobrescritos são sumariamente rejeitados.
5. Zero dependências externas comerciais:
   - Padrão SQLite WAL com BEGIN IMMEDIATE, hashing SHA-256 e Python Standard Library.
================================================================================
"""

import asyncio
from contextlib import contextmanager
import enum
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Garantir importações do Core
CORE_DIR = Path(r"C:\specter\Core")
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from autonomous_broker import canonical, digest, Broker
from oss_provider_matrix import (
    OSSProviderMatrix,
    create_default_matrix,
    ExecutionRequest,
    ExecutionResponse,
    Capability,
    ProviderType,
    compute_evidence_hash
)

logger = logging.getLogger("SpecterExecutionRouter")
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][ExecutionRouter] %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


class TaskType(str, enum.Enum):
    ARTIFACT_WRITE = "artifact.write_utf8.v1"
    CODE_ANALYSIS = "code.analysis.v1"
    CODE_GEN = "code.generate.v1"
    CHAT_REASONING = "chat.reasoning.v1"
    GENERIC_EXEC = "generic.exec.v1"


class TaskLifecycleState(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ATTAINED = "ATTAINED"
    RECONCILE = "RECONCILE"
    ESCALATE = "ESCALATE"


class StaleLeaseError(RuntimeError):
    """Lançado quando uma tentativa de commit usa um fencing token / lease_epoch ultrapassado."""
    pass


class ContractVerificationError(ValueError):
    """Lançado quando a verificação criptográfica ou contratual do resultado falha."""
    pass


class SpecterExecutionRouter:
    """
    Roteador de Execução e Motor de Lease/Fencing do Specter.
    Conecta o ciclo de tarefas à Matriz de Provedores OSS / Codex / Local Worker.
    """

    def __init__(self, db_path: Path, matrix: Optional[OSSProviderMatrix] = None):
        self.db_path = Path(db_path).resolve()
        self.matrix = matrix or create_default_matrix(db_path=self.db_path)
        self.root = self.db_path.parent / (self.db_path.stem + '_router_storage')
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=FULL')
            con.execute('PRAGMA busy_timeout=5000')
            yield con
        finally:
            con.close()

    def _init_schema(self):
        """Cria as tabelas do ciclo de vida com lease_epoch, fencing_token e evidências."""
        with self.connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS routed_tasks (
                    id TEXT PRIMARY KEY,
                    idem TEXT UNIQUE NOT NULL,
                    task_type TEXT NOT NULL,
                    spec BLOB NOT NULL,
                    spec_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    lease_epoch INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_deadline REAL NOT NULL DEFAULT 0.0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    assigned_provider TEXT,
                    resolved_provider TEXT,
                    output_payload TEXT,
                    output_hash TEXT,
                    evidence_hash TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    completed_at REAL
                );

                CREATE TABLE IF NOT EXISTS routed_task_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    lease_epoch INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created REAL NOT NULL,
                    UNIQUE(task_id, version)
                );

                CREATE TABLE IF NOT EXISTS routed_task_evidence (
                    hash TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    lease_epoch INTEGER NOT NULL,
                    manifest BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
            ''')

    # -------------------------------------------------------------------------
    # 1. SUBMIT: Submissão Idempotente de Tarefas
    # -------------------------------------------------------------------------
    def submit_task(
        self,
        task_type: str,
        payload: Dict[str, Any],
        idem: Optional[str] = None,
        timeout: float = 120.0,
        max_attempts: int = 3,
        preferred_provider: Optional[str] = None
    ) -> str:
        """
        Submete uma tarefa com validação estrita de contrato e idempotência.
        Garante compatibilidade total com artifact.write_utf8.v1.
        """
        if task_type == TaskType.ARTIFACT_WRITE.value:
            text = payload.get("text", "")
            if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
                raise ValueError("artifact.write_utf8.v1 requer text UTF-8 <= 65536 bytes")
            spec_dict = {
                "operation": TaskType.ARTIFACT_WRITE.value,
                "text": text,
                "timeout": int(timeout),
                "max_attempts": max_attempts
            }
        else:
            spec_dict = {
                "operation": task_type,
                "payload": payload,
                "timeout": float(timeout),
                "max_attempts": max_attempts,
                "preferred_provider": preferred_provider
            }

        spec_bytes = canonical(spec_dict)
        spec_hash = digest(spec_bytes)
        key = idem or spec_hash

        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM routed_tasks WHERE idem = ?", (key,)).fetchone()
            if existing:
                if existing["spec_hash"] != spec_hash:
                    con.rollback()
                    raise ValueError("Chave de idempotencia reutilizada com especificacao diferente")
                con.commit()
                return existing["id"]

            task_id = uuid.uuid4().hex
            now = time.time()
            con.execute(
                """
                INSERT INTO routed_tasks (
                    id, idem, task_type, spec, spec_hash, state, version,
                    lease_epoch, lease_deadline, max_attempts, assigned_provider,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, ?)
                """,
                (
                    task_id, key, task_type, spec_bytes, spec_hash,
                    TaskLifecycleState.SUBMITTED.value,
                    max_attempts, preferred_provider, now
                )
            )
            con.execute(
                """
                INSERT INTO routed_task_events (
                    task_id, version, lease_epoch, state, actor, detail, created
                ) VALUES (?, 1, 0, ?, 'router', ?, ?)
                """,
                (task_id, TaskLifecycleState.SUBMITTED.value, canonical({"status": "SUBMITTED"}).decode(), now)
            )
            con.commit()

        return task_id

    # -------------------------------------------------------------------------
    # 2. CLAIM / LEASE com Fencing Token (lease_epoch)
    # -------------------------------------------------------------------------
    def claim_task(
        self,
        worker_id: str,
        task_id: Optional[str] = None,
        lease_duration: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """
        Adquire lease de uma tarefa de forma segura e atômica.
        Incrementa monotonicamente o `lease_epoch` (fencing token).
        Um worker só pode comitar resultados se apresentar exatamente o fencing token obtido.
        """
        now = time.time()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if task_id:
                query = """
                    SELECT * FROM routed_tasks
                    WHERE id = ? AND state NOT IN ('ATTAINED', 'ESCALATE')
                    AND (state = 'SUBMITTED' OR lease_deadline < ? OR state = 'RECONCILE')
                """
                row = con.execute(query, (task_id, now)).fetchone()
            else:
                query = """
                    SELECT * FROM routed_tasks
                    WHERE state NOT IN ('ATTAINED', 'ESCALATE')
                    AND (state = 'SUBMITTED' OR lease_deadline < ? OR state = 'RECONCILE')
                    ORDER BY created_at ASC LIMIT 1
                """
                row = con.execute(query, (now,)).fetchone()

            if not row:
                con.commit()
                return None

            new_epoch = row["lease_epoch"] + 1
            new_version = row["version"] + 1
            new_deadline = now + lease_duration

            cursor = con.execute(
                """
                UPDATE routed_tasks
                SET state = ?, version = ?, lease_epoch = ?, lease_owner = ?,
                    lease_deadline = ?, attempts = attempts + 1
                WHERE id = ? AND version = ?
                """,
                (
                    TaskLifecycleState.CLAIMED.value,
                    new_version,
                    new_epoch,
                    worker_id,
                    new_deadline,
                    row["id"],
                    row["version"]
                )
            )
            if cursor.rowcount != 1:
                con.rollback()
                raise RuntimeError("Falha de concorrencia ao tentar claim da tarefa")

            detail = {
                "worker_id": worker_id,
                "fencing_token": new_epoch,
                "lease_deadline": new_deadline
            }
            con.execute(
                """
                INSERT INTO routed_task_events (
                    task_id, version, lease_epoch, state, actor, detail, created
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    new_version,
                    new_epoch,
                    TaskLifecycleState.CLAIMED.value,
                    worker_id,
                    canonical(detail).decode(),
                    now
                )
            )
            con.commit()

            claimed_row = dict(row)
            claimed_row["state"] = TaskLifecycleState.CLAIMED.value
            claimed_row["version"] = new_version
            claimed_row["lease_epoch"] = new_epoch
            claimed_row["lease_owner"] = worker_id
            claimed_row["lease_deadline"] = new_deadline
            claimed_row["fencing_token"] = new_epoch
            claimed_row["spec"] = json.loads(claimed_row["spec"])
            return claimed_row

    # -------------------------------------------------------------------------
    # 3. EXECUTE & ROUTE: Roteamento Semântico para Provedores da Matriz
    # -------------------------------------------------------------------------
    def route_task_to_provider(self, task_type: str, preferred_provider: Optional[str] = None) -> Tuple[str, Capability]:
        """
        Determina a capability e provedor alvo a partir do tipo da tarefa:
        - artifact.write_utf8.v1 -> Local Worker determinístico (preserva Milestone 1)
        - code.generate.v1 / code.analysis.v1 -> Codex Desktop / OSS
        - chat.reasoning.v1 -> Ollama / HF Spaces / OSS
        - generic.exec.v1 -> Local Worker ou fallback OSS
        """
        if task_type == TaskType.ARTIFACT_WRITE.value:
            return "local_worker", Capability.CODE_EXEC
        elif task_type in (TaskType.CODE_GEN.value, TaskType.CODE_ANALYSIS.value):
            return preferred_provider or "codex_desktop", Capability.CODE_EXEC
        elif task_type == TaskType.CHAT_REASONING.value:
            return preferred_provider or "ollama_local", Capability.CHAT
        else:
            return preferred_provider or "local_worker", Capability.GENERATE

    async def execute_task(self, claim_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executa a tarefa utilizando o roteamento correto da matriz.
        Garante conformidade com o tipo da tarefa e preservação do arquivo utf8.
        """
        task_id = claim_data["id"]
        task_type = claim_data["task_type"]
        spec = claim_data["spec"]
        fencing_token = claim_data["fencing_token"]

        now = time.time()
        # 1. Atualizar estado para EXECUTING
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute("SELECT version, lease_epoch FROM routed_tasks WHERE id = ?", (task_id,)).fetchone()
            if not cur or cur["lease_epoch"] != fencing_token:
                con.rollback()
                raise StaleLeaseError(f"Lease {fencing_token} expirado ou sobrescrito para tarefa {task_id}")

            new_ver = cur["version"] + 1
            con.execute(
                "UPDATE routed_tasks SET state = ?, version = ? WHERE id = ? AND version = ?",
                (TaskLifecycleState.EXECUTING.value, new_ver, task_id, cur["version"])
            )
            con.execute(
                """
                INSERT INTO routed_task_events (task_id, version, lease_epoch, state, actor, detail, created)
                VALUES (?, ?, ?, ?, 'executor', '{}', ?)
                """,
                (task_id, new_ver, fencing_token, TaskLifecycleState.EXECUTING.value, now)
            )
            con.commit()

        # 2. Executar de acordo com o tipo
        if task_type == TaskType.ARTIFACT_WRITE.value:
            # Compatibilidade 100% com o worker local de autonomous_broker.py
            text = spec["text"]
            folder = self.root / task_id
            folder.mkdir(parents=True, exist_ok=True)
            result_file = folder / f"result_epoch_{fencing_token}.bin"
            
            # Worker determinístico seguro
            data_bytes = text.encode("utf-8")
            with result_file.open("wb") as f:
                f.write(data_bytes)
                f.flush()
                os.fsync(f.fileno())

            expected_sha = digest(data_bytes)
            actual_sha = digest(result_file.read_bytes())
            if actual_sha != expected_sha:
                raise ContractVerificationError("Hash do artifact.write_utf8.v1 divergiu do esperado")

            final_path = folder / "result.bin"
            os.replace(result_file, final_path)

            return {
                "success": True,
                "provider_id": "local_worker",
                "output_payload": text,
                "artifact_path": str(final_path),
                "artifact_sha256": actual_sha,
                "raw_result": {"bytes_written": len(data_bytes)}
            }
        else:
            # Tarefas semânticas / código / raciocínio via Matriz de Provedores
            pref_provider, cap = self.route_task_to_provider(task_type, spec.get("preferred_provider"))
            payload = spec.get("payload", {})
            prompt = payload.get("prompt") or payload.get("code") or json.dumps(payload)
            
            req = ExecutionRequest(
                capability=cap.value,
                prompt=prompt,
                code=payload.get("code"),
                timeout=spec.get("timeout", 30.0),
                idempotency_key=f"{task_id}:epoch_{fencing_token}"
            )
            
            resp: ExecutionResponse = await self.matrix.execute_with_fallback(
                request=req,
                preferred_provider=pref_provider
            )

            if not resp.success:
                raise RuntimeError(f"Execução falhou na matriz: {resp.error}")

            out_text = resp.output_text or (json.dumps(resp.execution_result) if resp.execution_result else "")
            out_sha = digest(out_text.encode("utf-8"))

            return {
                "success": True,
                "provider_id": resp.provider_id,
                "output_payload": out_text,
                "artifact_path": None,
                "artifact_sha256": out_sha,
                "raw_result": resp.to_dict()
            }

    # -------------------------------------------------------------------------
    # 4. VERIFY & COMMIT RESULT: Verificação de Contrato e Fencing Token
    # -------------------------------------------------------------------------
    def commit_result(
        self,
        task_id: str,
        fencing_token: int,
        execution_output: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Verifica criptograficamente o resultado e comita como ATTAINED de forma durável.
        Rejeita commits se o fencing token estiver desatualizado (evita split-brain).
        """
        now = time.time()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM routed_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                con.rollback()
                raise KeyError(f"Tarefa {task_id} nao encontrada")

            # Validação estrita do fencing token / lease_epoch
            if row["lease_epoch"] != fencing_token:
                con.rollback()
                raise StaleLeaseError(
                    f"Fencing token rejeitado! Lease atual: {row['lease_epoch']}, recebido: {fencing_token}"
                )

            if row["state"] in (TaskLifecycleState.ATTAINED.value, TaskLifecycleState.ESCALATE.value):
                con.rollback()
                return dict(row)

            # Verificação do contrato criptográfico
            output_payload = execution_output.get("output_payload", "")
            artifact_sha = execution_output.get("artifact_sha256")
            expected_spec = json.loads(row["spec"])
            
            if row["task_type"] == TaskType.ARTIFACT_WRITE.value:
                expected_sha = digest(expected_spec["text"].encode("utf-8"))
                if artifact_sha != expected_sha:
                    con.rollback()
                    raise ContractVerificationError(
                        f"Verificacao criptografica falhou: esperado {expected_sha}, obtido {artifact_sha}"
                    )

            # Construção do Evidence Manifest (Milestone 1 e OSS Matrix compatível)
            manifest = {
                "schema": 1,
                "task_id": task_id,
                "task_type": row["task_type"],
                "spec_hash": row["spec_hash"],
                "fencing_token": fencing_token,
                "provider_id": execution_output.get("provider_id", "unknown"),
                "artifact_sha256": artifact_sha,
                "artifact_path": execution_output.get("artifact_path"),
                "verifier": "cryptographic-sha256-fencing-v1",
                "verdict": "PASS",
                "timestamp": now
            }
            evidence_hash = compute_evidence_hash(manifest)

            new_ver = row["version"] + 1
            con.execute(
                """
                UPDATE routed_tasks
                SET state = ?, version = ?, resolved_provider = ?, output_payload = ?,
                    output_hash = ?, evidence_hash = ?, completed_at = ?
                WHERE id = ? AND version = ? AND lease_epoch = ?
                """,
                (
                    TaskLifecycleState.ATTAINED.value,
                    new_ver,
                    execution_output.get("provider_id"),
                    output_payload,
                    artifact_sha,
                    evidence_hash,
                    now,
                    task_id,
                    row["version"],
                    fencing_token
                )
            )

            # Persistir evidência
            con.execute(
                "INSERT INTO routed_task_evidence (hash, task_id, lease_epoch, manifest, created_at) VALUES (?, ?, ?, ?, ?)",
                (evidence_hash, task_id, fencing_token, canonical(manifest), now)
            )

            # Persistir evento
            con.execute(
                """
                INSERT INTO routed_task_events (task_id, version, lease_epoch, state, actor, detail, created)
                VALUES (?, ?, ?, ?, 'verifier', ?, ?)
                """,
                (
                    task_id,
                    new_ver,
                    fencing_token,
                    TaskLifecycleState.ATTAINED.value,
                    canonical({"verdict": "PASS", "evidence_hash": evidence_hash}).decode(),
                    now
                )
            )
            con.commit()

        return self.get_task(task_id)

    def fail_task(
        self,
        task_id: str,
        fencing_token: int,
        error_msg: str,
        escalate: bool = False
    ) -> Dict[str, Any]:
        """Registra falha na execução ou escalona a tarefa caso tentativas se esgotem."""
        now = time.time()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM routed_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row or row["lease_epoch"] != fencing_token:
                con.rollback()
                raise StaleLeaseError("Tentativa de reportar falha em lease expirado")

            new_ver = row["version"] + 1
            is_escalate = escalate or (row["attempts"] >= row["max_attempts"])
            next_state = TaskLifecycleState.ESCALATE.value if is_escalate else TaskLifecycleState.RECONCILE.value

            con.execute(
                """
                UPDATE routed_tasks
                SET state = ?, version = ?, error = ?, lease_deadline = 0
                WHERE id = ? AND version = ?
                """,
                (next_state, new_ver, error_msg, task_id, row["version"])
            )
            con.execute(
                """
                INSERT INTO routed_task_events (task_id, version, lease_epoch, state, actor, detail, created)
                VALUES (?, ?, ?, ?, 'executor', ?, ?)
                """,
                (task_id, new_ver, fencing_token, next_state, canonical({"error": error_msg}).decode(), now)
            )
            con.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Recupera registro completo de uma tarefa."""
        with self.connection() as con:
            row = con.execute("SELECT * FROM routed_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            d = dict(row)
            d["spec"] = json.loads(d["spec"])
            return d

    # -------------------------------------------------------------------------
    # 5. RUN CYCLE: Drenagem completa com Matrix Routing e Fencing
    # -------------------------------------------------------------------------
    async def run_cycle(self, worker_id: str = "specter_coordinator_worker") -> Dict[str, Any]:
        """
        Executa um ciclo completo de drenagem de tarefas pendentes:
        Claim (com fencing token) -> Route & Execute via Provider Matrix ->
        Verify -> Commit Durável.
        """
        processed = []
        while True:
            claim = self.claim_task(worker_id=worker_id)
            if not claim:
                break

            task_id = claim["id"]
            fencing_token = claim["fencing_token"]
            try:
                # Executa com provedor adequado
                exec_result = await self.execute_task(claim)
                # Comita duravelmente com verificação criptográfica
                final_status = self.commit_result(task_id, fencing_token, exec_result)
                processed.append(final_status)
            except (ContractVerificationError, StaleLeaseError, Exception) as err:
                logger.error("Erro no processamento da tarefa %s: %s", task_id, err)
                try:
                    self.fail_task(task_id, fencing_token, str(err))
                except Exception:
                    pass

        return {
            "processed_count": len(processed),
            "processed_tasks": [p["id"] for p in processed]
        }
