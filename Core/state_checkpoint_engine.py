# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER STATE CHECKPOINT & EVENT SOURCING ENGINE (v1.1)
Formulado pelo Astra (GPT-6) em Turno 10 do Dialogo Tecnico
Refinado na Auditoria da Thread B (Ordinal #73)
Equacao Fundamental: S_t = (G, P, T, M, A, X, C)
Cadeia Hash Imutavel: h_i = SHA256(domain || h_{i-1} || canonical(e_i))
Criterio de Restauracao: Restore(C_k, E_{k+1:m}) == S_m
Bloqueio de Concorrencia Otimista: expected_version == current_aggregate_version + 1
================================================================================
"""

import os
import sys
import json
import time
import uuid
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

DOMAIN_TAG = b"SPECTER/EVENT/v1\0"
DEFAULT_DB_PATH = Path(r"C:\specter\Core\storage\specter_fabric.sqlite3")
BLOB_STORE_PATH = Path(r"C:\specter\Core\storage\blobs")


class ConcurrencyError(Exception):
    """Lancada quando ha divergencia de versao esperada (Optimistic Concurrency Conflict)."""
    pass


def canonical_json(obj: Any) -> bytes:
    """RFC 8785 canonical deterministic JSON encoding."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def sha256_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class StateSnapshot:
    """S_t = (G, P, T, M, A, X, C)"""
    G: Dict[str, Any] = field(default_factory=dict)  # Goals, restrictions, acceptance criteria
    P: List[Dict[str, Any]] = field(default_factory=list)  # Plan, steps, dependencies
    T: Dict[str, Any] = field(default_factory=dict)  # Tasks, attempts, versions, leases
    M: List[Dict[str, Any]] = field(default_factory=list)  # Messages and tool outputs
    A: Dict[str, str] = field(default_factory=dict)  # Artifacts: filename -> sha256
    X: Dict[str, Any] = field(default_factory=dict)  # External effects and confirmation states
    C: Dict[str, Any] = field(default_factory=dict)  # Configuration of models, tools, verifiers
    sequence: int = 0
    state_root_hash: str = ""

    def compute_root_hash(self) -> str:
        state_dict = {
            "G": self.G,
            "P": self.P,
            "T": self.T,
            "M": self.M,
            "A": self.A,
            "X": self.X,
            "C": self.C,
            "sequence": self.sequence
        }
        return sha256_digest(canonical_json(state_dict))


@dataclass
class EventRecord:
    event_id: str
    sequence: int
    aggregate_id: str
    expected_version: int
    event_type: str
    payload: Dict[str, Any]
    payload_hash: str
    previous_event_hash: str
    event_hash: str
    created_at: float

    @classmethod
    def create(cls, aggregate_id: str, sequence: int, expected_version: int,
               event_type: str, payload: Dict[str, Any], previous_event_hash: str = "0" * 64) -> 'EventRecord':
        ev_id = str(uuid.uuid4())
        c_payload = canonical_json(payload)
        p_hash = sha256_digest(c_payload)
        ts = time.time()
        
        event_dict = {
            "aggregate_id": aggregate_id,
            "event_id": ev_id,
            "expected_version": expected_version,
            "payload_hash": p_hash,
            "previous_event_hash": previous_event_hash,
            "sequence": sequence,
            "type": event_type
        }
        raw_to_hash = DOMAIN_TAG + previous_event_hash.encode('ascii') + canonical_json(event_dict)
        ev_hash = sha256_digest(raw_to_hash)

        return cls(
            event_id=ev_id,
            sequence=sequence,
            aggregate_id=aggregate_id,
            expected_version=expected_version,
            event_type=event_type,
            payload=payload,
            payload_hash=p_hash,
            previous_event_hash=previous_event_hash,
            event_hash=ev_hash,
            created_at=ts
        )


class StateCheckpointEngine:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH, blob_store: Path = BLOB_STORE_PATH):
        self.db_path = Path(db_path).resolve()
        self.blob_store = Path(blob_store).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_store.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_tables(self):
        with closing(self._get_connection()) as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS event_log (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                aggregate_id TEXT NOT NULL,
                expected_version INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                previous_event_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS state_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                parent_checkpoint_hash TEXT NOT NULL,
                state_root_hash TEXT NOT NULL,
                state_json TEXT NOT NULL,
                artifact_manifest_hash TEXT NOT NULL,
                configuration_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_event_log_agg ON event_log(aggregate_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_checkpoints_seq ON state_checkpoints(sequence);
            """)
            conn.commit()

    def store_blob(self, content: bytes) -> str:
        """Passo 1 e 2: PREPARE_BLOBS -> DURABLE_BLOBS"""
        b_hash = sha256_digest(content)
        target = self.blob_store / f"{b_hash}.blob"
        if not target.exists():
            tmp = self.blob_store / f"{b_hash}.tmp.{uuid.uuid4().hex}"
            tmp.write_bytes(content)
            tmp.replace(target)
        return b_hash

    def append_event(self, aggregate_id: str, event_type: str, payload: Dict[str, Any], expected_version: int) -> EventRecord:
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        with closing(self._get_connection()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            
            # Auditoria Astra Thread B: Validacao de Concorrencia Otimista
            cursor.execute("SELECT expected_version FROM event_log WHERE aggregate_id = ? ORDER BY sequence DESC LIMIT 1", (aggregate_id,))
            agg_row = cursor.fetchone()
            current_agg_ver = agg_row['expected_version'] if agg_row else 0
            
            if expected_version != current_agg_ver + 1:
                raise ConcurrencyError(
                    f"Conflito de Concorrencia Otimista para agregado '{aggregate_id}': "
                    f"versao esperada {expected_version}, mas versao atual e {current_agg_ver}."
                )

            cursor.execute("SELECT sequence, event_hash FROM event_log ORDER BY sequence DESC LIMIT 1")
            row = cursor.fetchone()
            
            if row:
                last_seq = row['sequence']
                prev_hash = row['event_hash']
            else:
                last_seq = 0
                prev_hash = "0" * 64
                
            next_seq = last_seq + 1
            record = EventRecord.create(
                aggregate_id=aggregate_id,
                sequence=next_seq,
                expected_version=expected_version,
                event_type=event_type,
                payload=payload,
                previous_event_hash=prev_hash
            )
            
            cursor.execute("""
            INSERT INTO event_log (
                sequence, event_id, aggregate_id, expected_version, event_type,
                payload_json, payload_hash, previous_event_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.sequence, record.event_id, record.aggregate_id, record.expected_version,
                record.event_type, json.dumps(record.payload, ensure_ascii=True),
                record.payload_hash, record.previous_event_hash, record.event_hash, record.created_at
            ))
            conn.commit()
            return record

    def get_latest_aggregate_version(self, aggregate_id: str) -> int:
        """Retorna a versao mais recente registrada para um aggregate_id (0 se nenhuma)."""
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT expected_version FROM event_log WHERE aggregate_id = ? ORDER BY sequence DESC LIMIT 1", (aggregate_id,))
            row = cursor.fetchone()
            return row['expected_version'] if row else 0

    def get_aggregate_events(self, aggregate_id: str) -> List[EventRecord]:
        """Retorna todos os eventos registrados para um aggregate_id em ordem cronologica."""
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM event_log WHERE aggregate_id = ? ORDER BY sequence ASC", (aggregate_id,))
            rows = cursor.fetchall()
            return [
                EventRecord(
                    sequence=r['sequence'],
                    event_id=r['event_id'],
                    aggregate_id=r['aggregate_id'],
                    expected_version=r['expected_version'],
                    event_type=r['event_type'],
                    payload=json.loads(r['payload_json']),
                    payload_hash=r['payload_hash'],
                    previous_event_hash=r['previous_event_hash'],
                    event_hash=r['event_hash'],
                    created_at=r['created_at']
                )
                for r in rows
            ]

    def create_checkpoint(self, state: StateSnapshot) -> Dict[str, Any]:
        with closing(self._get_connection()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            cursor.execute("SELECT state_root_hash FROM state_checkpoints ORDER BY sequence DESC LIMIT 1")
            row = cursor.fetchone()
            parent_hash = row['state_root_hash'] if row else "0" * 64

            state.state_root_hash = state.compute_root_hash()
            chk_id = str(uuid.uuid4())
            c_hash = sha256_digest(canonical_json(state.C))
            a_hash = sha256_digest(canonical_json(state.A))
            state_json = json.dumps(asdict(state), ensure_ascii=True)
            ts = time.time()

            cursor.execute("""
            INSERT INTO state_checkpoints (
                checkpoint_id, sequence, parent_checkpoint_hash, state_root_hash,
                state_json, artifact_manifest_hash, configuration_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chk_id, state.sequence, parent_hash, state.state_root_hash,
                state_json, a_hash, c_hash, ts
            ))
            conn.commit()

            return {
                "checkpoint_id": chk_id,
                "sequence": state.sequence,
                "state_root_hash": state.state_root_hash,
                "parent_checkpoint_hash": parent_hash,
                "artifact_manifest_hash": a_hash,
                "configuration_hash": c_hash,
                "created_at": ts
            }

    def restore_state(self, target_sequence: Optional[int] = None) -> StateSnapshot:
        """VALIDATE -> LOAD_BASE -> REPLAY -> RECONCILE -> RESUME"""
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            if target_sequence is not None:
                cursor.execute("SELECT * FROM state_checkpoints WHERE sequence <= ? ORDER BY sequence DESC LIMIT 1", (target_sequence,))
            else:
                cursor.execute("SELECT * FROM state_checkpoints ORDER BY sequence DESC LIMIT 1")
            chk_row = cursor.fetchone()

            if chk_row:
                raw_state = json.loads(chk_row['state_json'])
                state = StateSnapshot(**raw_state)
                base_seq = chk_row['sequence']
            else:
                state = StateSnapshot()
                base_seq = 0

            # Replay events post-checkpoint
            if target_sequence is not None:
                cursor.execute("SELECT * FROM event_log WHERE sequence > ? AND sequence <= ? ORDER BY sequence ASC", (base_seq, target_sequence))
            else:
                cursor.execute("SELECT * FROM event_log WHERE sequence > ? ORDER BY sequence ASC", (base_seq,))
            events = cursor.fetchall()

            for ev in events:
                payload = json.loads(ev['payload_json'])
                ev_type = ev['event_type']

                if ev_type == "goal.created":
                    state.G.update(payload)
                elif ev_type == "plan.step_added":
                    state.P.append(payload)
                elif ev_type == "task.state_changed":
                    t_id = payload.get("task_id", "default")
                    state.T[t_id] = payload
                elif ev_type == "message.recorded":
                    state.M.append(payload)
                elif ev_type == "artifact.registered":
                    state.A[payload["name"]] = payload["hash"]
                elif ev_type == "effect.confirmed":
                    state.X[payload["effect_id"]] = payload
                elif ev_type == "config.updated":
                    state.C.update(payload)

                state.sequence = ev['sequence']

            state.state_root_hash = state.compute_root_hash()
            return state
