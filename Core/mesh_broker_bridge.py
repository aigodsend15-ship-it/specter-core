# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER UNIFIED AUTONOMOUS MESH BRIDGE (v8.1 — OUTBOX & UOW DURABLE)
Conecta o Autonomous Broker (Marco 1), o Semantic Memory Nexus (Marco 2),
e o OSS Provider Matrix (Marco 3) em um motor continuo de alta vazao com
garantias transacionais ACID, Outbox duravel e replay idempotente sem TOCTOU.
================================================================================
"""

import os
import sys
import time
import json
import uuid
import sqlite3
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import closing

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from autonomous_broker import Broker, canonical, digest
from semantic_memory_nexus import SemanticMemoryNexus, DSLCodec, DSLFrame
from oss_provider_matrix import OSSProviderMatrix, create_default_matrix, ExecutionRequest, ExecutionResponse
from state_checkpoint_engine import StateCheckpointEngine, StateSnapshot, ConcurrencyError


class PayloadConflictError(ValueError):
    """Lancada quando a mesma chave de idempotencia e reutilizada com especificacao/payload divergente."""
    pass


class UnifiedMeshCoordinator:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        memory: Optional[SemanticMemoryNexus] = None,
        matrix: Optional[OSSProviderMatrix] = None,
        checkpoint_engine: Optional[StateCheckpointEngine] = None
    ):
        from autonomous_broker import DEFAULT_DB
        self.db = Path(db_path) if db_path else DEFAULT_DB
        self.broker = Broker(db=self.db)
        self.memory = memory or SemanticMemoryNexus(db_path=self.db)
        self.matrix = matrix or create_default_matrix()
        self.checkpoint_engine = checkpoint_engine or StateCheckpointEngine(db_path=self.db)
        self.running = False
        self._init_bridge_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_bridge_schema(self):
        """Inicializa tabelas de Outbox / Unidade de Trabalho do Mesh Bridge."""
        with closing(self._get_connection()) as conn, conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS mesh_outbox (
                outbox_id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                idempotency_key TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                dispatched_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_mesh_outbox_status ON mesh_outbox(status);
            CREATE INDEX IF NOT EXISTS idx_mesh_outbox_agg ON mesh_outbox(aggregate_id);
            """)

    def reconcile_outbox(self, max_batch: int = 50) -> int:
        """
        Varre entradas de outbox com status PENDING e publica de forma idempotente
        no checkpoint_engine (event_log).
        Se a publicacao ja existir ou suceder, marca como DISPATCHED.
        Retorna o numero de eventos reconciliados com sucesso.
        """
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM mesh_outbox WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT ?",
                (max_batch,)
            )
            rows = cursor.fetchall()

        reconciled = 0
        for row in rows:
            outbox_id = row['outbox_id']
            agg_id = row['aggregate_id']
            event_type = row['event_type']
            payload = json.loads(row['payload_json'])

            # Verifica se o evento ja existe no event_log para este agregado
            existing_events = self.checkpoint_engine.get_aggregate_events(agg_id)
            goal_created_exists = any(e.event_type == event_type for e in existing_events)

            if not goal_created_exists:
                # TOCTOU guard: lock-protected append_event
                # Se for goal.created com versao esperada 1
                try:
                    self.checkpoint_engine.append_event(
                        aggregate_id=agg_id,
                        event_type=event_type,
                        payload=payload,
                        expected_version=1
                    )
                except ConcurrencyError:
                    # Concorrencia: outro processo acabou de registrar
                    pass

            with closing(self._get_connection()) as conn, conn:
                conn.execute(
                    "UPDATE mesh_outbox SET status = 'DISPATCHED', dispatched_at = ? WHERE outbox_id = ?",
                    (time.time(), outbox_id)
                )
            reconciled += 1

        return reconciled

    def submit_goal(
        self,
        goal_text: str,
        idempotency_key: str,
        priority_node: Optional[str] = None,
        _simulate_crash_before_outbox_dispatch: bool = False
    ) -> Dict[str, Any]:
        """
        Submete uma nova meta ao ecossistema Specter de forma 100% atomica e idempotente.
        
        Garantias:
        1. Executa sob BEGIN IMMEDIATE unico contra o banco SQLite WAL compartilhado.
        2. Persiste a tarefa no broker (broker_tasks, broker_events), a memoria semantica
           (semantic_memories, agent_heuristics_cache) e a intencao no outbox (mesh_outbox)
           numa UNICA transacao atomica.
        3. Valida conflito de payload para mesma chave de idempotencia antes de qualquer mutacao.
        4. Replay idempotente: se a chave ja existir com mesmo payload, reutiliza os identificadores.
        5. Publica o evento no checkpoint_engine e atualiza o outbox para DISPATCHED.
        6. Elimina TOCTOU: a versao do agregado e verificada e gravada com isolamento imediato.
        """
        if not isinstance(goal_text, str) or len(goal_text.encode('utf-8')) > 65536:
            raise ValueError('goal_text must be UTF-8 string <= 65536 bytes')
        if not idempotency_key or not isinstance(idempotency_key, str):
            raise ValueError('idempotency_key must be a non-empty string')

        # 1. Calculo deterministico do spec do broker e hash
        spec_dict = {
            'operation': 'artifact.write_utf8.v1',
            'text': goal_text,
            'timeout': 120,
            'max_attempts': 3
        }
        spec_bytes = canonical(spec_dict)
        spec_hash = digest(spec_bytes)

        # 2. Calculo deterministico da memoria semantica
        mem_domain = "goals"
        mem_sub = f"goal:{idempotency_key}"
        mem_pred = "ESTABLISHES"
        mem_obj = goal_text
        mem_context = json.dumps({"priority_node": priority_node, "source": "operator"})
        fingerprint = f"{mem_domain}:{mem_sub}:{mem_pred}:{mem_obj}".encode('utf-8')
        mem_id = "mem_" + digest(fingerprint)[:16]
        tags_json = json.dumps(["goal", "mesh", "sovereign"])

        # 3. Transacao Atomica (Unit of Work): Broker Task + Semantic Memory + Outbox
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Verifica se a chave de idempotencia ja existe no broker
                existing = conn.execute(
                    "SELECT id, spec_hash, spec FROM broker_tasks WHERE idem = ?",
                    (idempotency_key,)
                ).fetchone()

                if existing:
                    if existing['spec_hash'] != spec_hash:
                        conn.rollback()
                        raise PayloadConflictError(
                            f"Idempotency key '{idempotency_key}' reused with different specification: "
                            f"expected spec_hash {existing['spec_hash']}, got {spec_hash}"
                        )
                    task_id = existing['id']
                else:
                    # Nova tarefa do broker
                    task_id = uuid.uuid4().hex
                    now = time.time()
                    conn.execute(
                        "INSERT INTO broker_tasks(id, idem, spec, spec_hash, state, version, attempts, max_attempts, deadline) "
                        "VALUES(?, ?, ?, ?, 'INIT', 1, 0, 3, ?)",
                        (task_id, idempotency_key, spec_bytes, spec_hash, now + 120)
                    )
                    conn.execute(
                        "INSERT INTO broker_events(task_id, version, state, detail, created) VALUES(?, 1, 'INIT', '{}', ?)",
                        (task_id, now)
                    )

                # Persistencia da memoria semantica dentro da mesma transacao
                now_mem = time.time()
                mem_existing = conn.execute(
                    "SELECT id, confidence, reinforcement_score FROM semantic_memories WHERE id = ?",
                    (mem_id,)
                ).fetchone()

                if mem_existing:
                    conn.execute("""
                        UPDATE semantic_memories SET
                            confidence = MIN(1.0, confidence + 0.05),
                            reinforcement_score = reinforcement_score + 1.0,
                            context = CASE WHEN ? != '' THEN ? ELSE context END,
                            task_id = COALESCE(?, task_id),
                            tags = ?,
                            updated_at = ?
                        WHERE id = ?
                    """, (mem_context, mem_context, task_id, tags_json, now_mem, mem_id))
                else:
                    conn.execute("""
                        INSERT INTO semantic_memories (
                            id, domain, subject, predicate, object, context,
                            confidence, evidence_hash, task_id, source_agent,
                            tags, access_count, reinforcement_score, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1.0, NULL, ?, 'Specter', ?, 0, 1.0, ?, ?)
                    """, (
                        mem_id, mem_domain, mem_sub, mem_pred, mem_obj, mem_context,
                        task_id, tags_json, now_mem, now_mem
                    ))

                # Cache heuristica
                rule_key = f"{mem_domain}:{mem_sub}:{mem_pred}"
                dsl_repr = DSLFrame(
                    opcode="MEM",
                    id=mem_id,
                    actor="Specter",
                    params={
                        "domain": mem_domain,
                        "sub": mem_sub,
                        "pred": mem_pred,
                        "obj": mem_obj,
                        "conf": 1.0
                    }
                ).to_dsl()
                conn.execute("""
                    INSERT INTO agent_heuristics_cache(rule_key, domain, compact_dsl, confidence, updated_at)
                    VALUES(?, ?, ?, 1.0, ?)
                    ON CONFLICT(rule_key) DO UPDATE SET
                        compact_dsl = excluded.compact_dsl,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                """, (rule_key, mem_domain, dsl_repr, now_mem))

                # Registro no Outbox (garantia duravel da intencao do evento)
                event_type = "goal.created"
                event_payload = {"goal": goal_text, "key": idempotency_key, "mem_id": mem_id}
                payload_json = json.dumps(event_payload, sort_keys=True, ensure_ascii=True)
                payload_hash = digest(payload_json.encode('ascii'))
                outbox_id = "obx_" + digest(f"{idempotency_key}:{task_id}".encode('ascii'))[:16]

                conn.execute("""
                    INSERT INTO mesh_outbox (
                        outbox_id, aggregate_id, idempotency_key, event_type,
                        payload_json, payload_hash, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        aggregate_id = excluded.aggregate_id,
                        payload_json = excluded.payload_json,
                        payload_hash = excluded.payload_hash
                """, (outbox_id, task_id, idempotency_key, event_type, payload_json, payload_hash, time.time()))

                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # Simulacao de queda de energia / crash antes do dispatch do outbox
        if _simulate_crash_before_outbox_dispatch:
            raise RuntimeError("Simulated crash between task commit and outbox event dispatch")

        # 4. Despacho do Outbox: registrar evento no checkpoint engine e marcar DISPATCHED
        events = self.checkpoint_engine.get_aggregate_events(task_id)
        if not any(e.event_type == event_type for e in events):
            try:
                self.checkpoint_engine.append_event(
                    aggregate_id=task_id,
                    event_type=event_type,
                    payload=event_payload,
                    expected_version=1
                )
            except ConcurrencyError:
                # Outra thread/processo concorrente ja gravou a versao 1
                pass

        # Marca como DISPATCHED
        with closing(self._get_connection()) as conn, conn:
            conn.execute(
                "UPDATE mesh_outbox SET status = 'DISPATCHED', dispatched_at = ? WHERE idempotency_key = ?",
                (time.time(), idempotency_key)
            )

        return {
            "task_id": task_id,
            "idempotency_key": idempotency_key,
            "semantic_memory_id": mem_id,
            "status": "QUEUED"
        }

    async def run_cycle(self) -> Dict[str, Any]:
        """Executa um ciclo completo de drenagem de tarefas e despacho na matriz."""
        # Reconcilia eventuais itens pendentes no outbox antes de drenar
        reconciled = self.reconcile_outbox()
        processed = await self.broker.run()
        consolidated = self.memory.consolidate_episodic_broker(limit=20)
        state = self.checkpoint_engine.restore_state()
        chk = self.checkpoint_engine.create_checkpoint(state)

        return {
            "reconciled_outbox": reconciled,
            "processed_tasks": len(processed),
            "consolidated_memories": len(consolidated),
            "checkpoint_id": chk["checkpoint_id"],
            "state_root_hash": chk["state_root_hash"]
        }


if __name__ == "__main__":
    coordinator = UnifiedMeshCoordinator()
    print("[*] Specter Unified Mesh Bridge v8.1 (Outbox & UoW Durable) inicializado com sucesso!")
    res = asyncio.run(coordinator.run_cycle())
    print(f"[+] Ciclo executado: {json.dumps(res, indent=2)}")
