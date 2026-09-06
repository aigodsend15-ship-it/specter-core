# -*- coding: utf-8 -*-
"""
SPECTER BRIDGE JOB STATE MACHINE (v1.0)
Independent, transport-agnostic correlation of jobs and results.
"""
import time
import json
import uuid
import hashlib
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from contextlib import closing


class JobState(str, Enum):
    CREATED = 'CREATED'
    ACCEPTED = 'ACCEPTED'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    TIMED_OUT = 'TIMED_OUT'
    UNKNOWN = 'UNKNOWN'


TERMINAL_STATES = {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.TIMED_OUT.value}

VALID_TRANSITIONS = {
    JobState.CREATED.value: {JobState.ACCEPTED.value, JobState.FAILED.value, JobState.UNKNOWN.value},
    JobState.ACCEPTED.value: {JobState.RUNNING.value, JobState.FAILED.value, JobState.TIMED_OUT.value, JobState.UNKNOWN.value},
    JobState.RUNNING.value: {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.TIMED_OUT.value, JobState.UNKNOWN.value},
    JobState.TIMED_OUT.value: set(),
    JobState.SUCCEEDED.value: set(),
    JobState.FAILED.value: set(),
    JobState.UNKNOWN.value: {JobState.FAILED.value}
}


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('utf-8')


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BridgeJobError(Exception):
    pass


class InvalidTransitionError(BridgeJobError):
    pass


class StaleLeaseEpochError(BridgeJobError):
    pass


class PayloadMismatchError(BridgeJobError):
    pass


class BridgeJobManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else Path(r'C:\specter\Core\storage\specter_fabric.sqlite3')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=FULL;')
        return conn

    def _init_schema(self):
        with closing(self._get_connection()) as conn, conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS bridge_jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE NOT NULL,
                message_id TEXT,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                lease_epoch INTEGER NOT NULL DEFAULT 0,
                assigned_worker TEXT,
                timeout_seconds REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                deadline REAL NOT NULL,
                evidence_hash TEXT,
                result_json TEXT,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bridge_jobs_state ON bridge_jobs(state);
            CREATE INDEX IF NOT EXISTS idx_bridge_jobs_key ON bridge_jobs(idempotency_key);
            """)

    def submit_job(
        self,
        idempotency_key: str,
        payload: Dict[str, Any],
        timeout_seconds: float = 60.0,
        message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        payload_bytes = canonical_json(payload)
        payload_hash = compute_sha256(payload_bytes)
        now = time.time()
        deadline = now + timeout_seconds

        with closing(self._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute(
                    'SELECT * FROM bridge_jobs WHERE idempotency_key = ?',
                    (idempotency_key,)
                ).fetchone()

                if row:
                    if row['payload_hash'] != payload_hash:
                        conn.rollback()
                        raise PayloadMismatchError(
                            f"Key '{idempotency_key}' reused with different payload."
                        )
                    conn.commit()
                    return dict(row)

                job_id = 'job_' + uuid.uuid4().hex[:16]
                initial_state = JobState.CREATED.value

                conn.execute("""
                INSERT INTO bridge_jobs (
                    job_id, idempotency_key, message_id, payload_json, payload_hash,
                    state, lease_epoch, timeout_seconds, created_at, updated_at, deadline
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """, (
                    job_id, idempotency_key, message_id, json.dumps(payload, ensure_ascii=True),
                    payload_hash, initial_state, timeout_seconds, now, now, deadline
                ))
                conn.commit()
                return self.get_job(job_id)
            except Exception:
                conn.rollback()
                raise

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with closing(self._get_connection()) as conn:
            row = conn.execute('SELECT * FROM bridge_jobs WHERE job_id = ?', (job_id,)).fetchone()
            if not row:
                raise KeyError(f"Job '{job_id}' not found")
            return dict(row)

    def claim_job(self, job_id: str, worker_id: str) -> Dict[str, Any]:
        now = time.time()
        with closing(self._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT * FROM bridge_jobs WHERE job_id = ?', (job_id,)).fetchone()
                if not row:
                    conn.rollback()
                    raise KeyError(f"Job '{job_id}' not found")

                current_state = row['state']
                if current_state in TERMINAL_STATES:
                    conn.rollback()
                    raise InvalidTransitionError(f"Cannot claim job in terminal state '{current_state}'")

                new_epoch = row['lease_epoch'] + 1
                new_state = JobState.ACCEPTED.value

                conn.execute("""
                UPDATE bridge_jobs
                SET state = ?, lease_epoch = ?, assigned_worker = ?, updated_at = ?
                WHERE job_id = ?
                """, (new_state, new_epoch, worker_id, now, job_id))
                conn.commit()
                return self.get_job(job_id)
            except Exception:
                conn.rollback()
                raise

    def start_running(self, job_id: str, lease_epoch: int) -> Dict[str, Any]:
        now = time.time()
        with closing(self._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT * FROM bridge_jobs WHERE job_id = ?', (job_id,)).fetchone()
                if not row:
                    conn.rollback()
                    raise KeyError(f"Job '{job_id}' not found")

                if row['lease_epoch'] != lease_epoch:
                    conn.rollback()
                    raise StaleLeaseEpochError(
                        f"Stale lease epoch ({lease_epoch}). Current: {row['lease_epoch']}"
                    )

                if row['state'] not in VALID_TRANSITIONS or JobState.RUNNING.value not in VALID_TRANSITIONS[row['state']]:
                    conn.rollback()
                    raise InvalidTransitionError(f"Invalid transition from {row['state']} to RUNNING")

                conn.execute("""
                UPDATE bridge_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ?
                """, (JobState.RUNNING.value, now, job_id))
                conn.commit()
                return self.get_job(job_id)
            except Exception:
                conn.rollback()
                raise

    def complete_job(
        self,
        job_id: str,
        lease_epoch: int,
        result: Dict[str, Any],
        evidence_hash: str
    ) -> Dict[str, Any]:
        now = time.time()
        if not evidence_hash or len(evidence_hash) != 64:
            raise ValueError('SUCCEEDED requires valid 64-char hex SHA-256 evidence')

        with closing(self._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT * FROM bridge_jobs WHERE job_id = ?', (job_id,)).fetchone()
                if not row:
                    conn.rollback()
                    raise KeyError(f"Job '{job_id}' not found")

                if row['lease_epoch'] != lease_epoch:
                    conn.rollback()
                    raise StaleLeaseEpochError(
                        f"Rejection of stale result: lease_epoch {lease_epoch} != {row['lease_epoch']}"
                    )

                if row['state'] != JobState.RUNNING.value:
                    conn.rollback()
                    raise InvalidTransitionError(f"Invalid transition from {row['state']} to SUCCEEDED")

                if now > row['deadline']:
                    conn.execute("""
                    UPDATE bridge_jobs
                    SET state = ?, updated_at = ?, error_message = 'Deadline exceeded before completion'
                    WHERE job_id = ?
                    """, (JobState.TIMED_OUT.value, now, job_id))
                    conn.commit()
                    return self.get_job(job_id)

                conn.execute("""
                UPDATE bridge_jobs
                SET state = ?, updated_at = ?, result_json = ?, evidence_hash = ?
                WHERE job_id = ?
                """, (JobState.SUCCEEDED.value, now, json.dumps(result, ensure_ascii=True), evidence_hash, job_id))
                conn.commit()
                return self.get_job(job_id)
            except Exception:
                conn.rollback()
                raise

    def fail_job(self, job_id: str, error_message: str, lease_epoch: Optional[int] = None) -> Dict[str, Any]:
        now = time.time()
        with closing(self._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT * FROM bridge_jobs WHERE job_id = ?', (job_id,)).fetchone()
                if not row:
                    conn.rollback()
                    raise KeyError(f"Job '{job_id}' not found")

                if lease_epoch is not None and row['lease_epoch'] != lease_epoch:
                    conn.rollback()
                    raise StaleLeaseEpochError('Stale lease epoch for fail')

                conn.execute("""
                UPDATE bridge_jobs
                SET state = ?, updated_at = ?, error_message = ?
                WHERE job_id = ?
                """, (JobState.FAILED.value, now, error_message, job_id))
                conn.commit()
                return self.get_job(job_id)
            except Exception:
                conn.rollback()
                raise

    def check_timeouts(self) -> int:
        now = time.time()
        with closing(self._get_connection()) as conn, conn:
            cur = conn.execute("""
            UPDATE bridge_jobs
            SET state = 'TIMED_OUT', updated_at = ?, error_message = 'Job deadline expired'
            WHERE state IN ('CREATED', 'ACCEPTED', 'RUNNING') AND deadline < ?
            """, (now, now))
            return cur.rowcount

    def list_pending_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
            SELECT * FROM bridge_jobs
            WHERE state = 'CREATED'
            ORDER BY created_at ASC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
