"""Milestone 1: local durable broker. No network, arbitrary shell or model calls."""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

DEFAULT_DB = Path(__file__).parent / 'storage' / 'specter_fabric.sqlite3'
TERMINAL = {'ATTAINED', 'ESCALATE'}
EDGES = {'INIT': {'PLAN'}, 'PLAN': {'EXEC'}, 'EXEC': {'VERIFY', 'RECONCILE'},
         'VERIFY': {'ATTAINED', 'RECONCILE'}, 'RECONCILE': {'PLAN'},
         'ATTAINED': set(), 'ESCALATE': set()}


def canonical(value):
    # Deterministic encoding for this restricted schema; not general RFC 8785 JCS.
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def exclusive(path):
    """OS-owned lock: released on process death, never inferred from stale PID."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def worker(spec_path, result_path):
    spec = json.loads(Path(spec_path).read_bytes())
    data = spec['text'].encode('utf-8')
    target = Path(result_path)
    with target.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class Broker:
    def __init__(self, db=DEFAULT_DB, event_bus=None):
        from specter_engine import SpecterEventBus
        self.db = Path(db).resolve()
        self.root = self.db.parent / (self.db.stem + '_broker')
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = event_bus or SpecterEventBus(capacity=100)
        with self.connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS broker_tasks (
                  id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, spec BLOB NOT NULL,
                  spec_hash TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                  deadline REAL NOT NULL, evidence_hash TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS broker_events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  version INTEGER NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL,
                  created REAL NOT NULL, UNIQUE(task_id,version));
                CREATE TABLE IF NOT EXISTS broker_evidence (
                  hash TEXT PRIMARY KEY, task_id TEXT NOT NULL, manifest BLOB NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=FULL')
            con.execute('PRAGMA busy_timeout=5000')
            yield con
        finally:
            con.close()

    def submit(self, text, idem=None, timeout=120, max_attempts=3):
        if not isinstance(text, str) or len(text.encode('utf-8')) > 65536:
            raise ValueError('text must be UTF-8 <= 65536 bytes')
        if not 1 <= timeout <= 86400 or not 1 <= max_attempts <= 10:
            raise ValueError('invalid limits')
        spec = canonical({'operation': 'artifact.write_utf8.v1', 'text': text,
                          'timeout': timeout, 'max_attempts': max_attempts})
        key = idem or digest(spec)
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            existing = con.execute('SELECT * FROM broker_tasks WHERE idem=?', (key,)).fetchone()
            if existing:
                if existing['spec_hash'] != digest(spec):
                    con.rollback()
                    raise ValueError('idempotency key reused with different specification')
                con.commit()
                return existing['id']
            task_id = uuid.uuid4().hex
            con.execute('INSERT INTO broker_tasks(id,idem,spec,spec_hash,state,version,max_attempts,deadline) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (task_id, key, spec, digest(spec), 'INIT', 1, max_attempts, time.time()+timeout))
            con.execute('INSERT INTO broker_events(task_id,version,state,detail,created) VALUES(?,?,?,?,?)',
                        (task_id, 1, 'INIT', '{}', time.time()))
            con.commit()
        return task_id

    def status(self, task_id):
        with self.connection() as con:
            row = con.execute('SELECT * FROM broker_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def transition(self, row, state, detail=None, evidence=None):
        if state not in EDGES[row['state']] and not (state == 'ESCALATE' and row['state'] not in TERMINAL):
            raise ValueError('invalid transition')
        detail = detail or {}
        evidence_hash = digest(b'SPECTER/EVIDENCE/v1\0' + canonical(evidence)) if evidence else None
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            cursor = con.execute('UPDATE broker_tasks SET state=?,version=version+1,attempts=attempts+?, '
                                 'evidence_hash=COALESCE(?,evidence_hash),error=? WHERE id=? AND version=?',
                                 (state, int(state == 'EXEC'), evidence_hash, detail.get('error'), row['id'], row['version']))
            if cursor.rowcount != 1:
                con.rollback()
                raise RuntimeError('concurrent transition')
            if evidence:
                con.execute('INSERT INTO broker_evidence VALUES(?,?,?)',
                            (evidence_hash, row['id'], canonical(evidence)))
            con.execute('INSERT INTO broker_events(task_id,version,state,detail,created) VALUES(?,?,?,?,?)',
                        (row['id'], row['version']+1, state, canonical(detail).decode(), time.time()))
            con.commit()
        # Notifications are advisory; broker_events is the durable source of truth.
        self.bus.push(2, 'LOCAL_BROKER', state, {'task_id': row['id']})
        return self.status(row['id'])

    def files(self, task_id):
        folder = self.root / task_id
        folder.mkdir(exist_ok=True)
        return folder / 'spec.json', folder / 'result.bin', folder / 'candidate.bin'

    def matches(self, path, expected):
        return path.is_file() and path.stat().st_size <= 65536 and digest(path.read_bytes()) == expected

    async def execute(self, row, checkpoint):
        spec_path, result, _ = self.files(row['id'])
        spec_path.write_bytes(row['spec'])
        # Each attempt has its own output: an orphan from an interrupted broker
        # cannot publish over a newer attempt. Worker is fixed code, no shell.
        attempt_output = result.with_name('attempt-' + uuid.uuid4().hex + '.bin')
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-I', str(Path(__file__).resolve()), '_worker',
            str(spec_path), str(attempt_output),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            await asyncio.wait_for(proc.wait(), timeout=max(.01, min(10, row['deadline']-time.time())))
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise RuntimeError('worker failed: ' + str(proc.returncode))
        expected = digest(json.loads(row['spec'])['text'].encode('utf-8'))
        if not self.matches(attempt_output, expected):
            raise RuntimeError('worker output mismatch')
        os.replace(attempt_output, result)
        await checkpoint('OUTPUT_COMMITTED', row)

    async def run(self, checkpoint=None):
        async def noop(stage, row):
            pass
        checkpoint = checkpoint or noop
        # Single local dispatcher per database; OS lock survives neither crash nor reboot.
        with exclusive(self.root / 'dispatcher.lock'):
            with self.connection() as con:
                ids = [r[0] for r in con.execute("SELECT id FROM broker_tasks WHERE state NOT IN ('ATTAINED','ESCALATE')")]
            for task_id in ids:
                row = self.status(task_id)
                if row['state'] in {'EXEC', 'VERIFY'}:
                    row = self.transition(row, 'RECONCILE', {'reason': 'restart'})
                while row['state'] not in TERMINAL:
                    await checkpoint(row['state'], row)
                    if time.time() >= row['deadline']:
                        row = self.transition(row, 'ESCALATE', {'error': 'deadline exceeded'})
                        break
                    spec = json.loads(row['spec'])
                    _, result, _ = self.files(task_id)
                    expected = digest(spec['text'].encode('utf-8'))
                    try:
                        if row['state'] == 'INIT':
                            row = self.transition(row, 'PLAN')
                        elif row['state'] == 'RECONCILE':
                            row = self.transition(row, 'PLAN', {'result_present': self.matches(result, expected)})
                        elif row['state'] == 'PLAN':
                            if row['attempts'] >= row['max_attempts'] and not self.matches(result, expected):
                                row = self.transition(row, 'ESCALATE', {'error': 'attempt limit'})
                            else:
                                row = self.transition(row, 'EXEC')
                        elif row['state'] == 'EXEC':
                            if not self.matches(result, expected):
                                await self.execute(row, checkpoint)
                            row = self.transition(row, 'VERIFY')
                        elif row['state'] == 'VERIFY':
                            if not self.matches(result, expected):
                                row = self.transition(row, 'RECONCILE', {'reason': 'hash mismatch'})
                            elif time.time() >= row['deadline']:
                                row = self.transition(row, 'ESCALATE', {'error': 'deadline exceeded'})
                            else:
                                manifest = {'schema': 1, 'task_id': task_id, 'spec_hash': row['spec_hash'],
                                            'artifact': str(result), 'artifact_sha256': expected,
                                            'verifier': 'exact-utf8-sha256-v1', 'verdict': 'PASS',
                                            'active_workers_owned_by_attempt': 0}
                                row = self.transition(row, 'ATTAINED', evidence=manifest)
                    except (OSError, RuntimeError, asyncio.TimeoutError) as error:
                        row = self.transition(row, 'ESCALATE', {'error': type(error).__name__ + ': ' + str(error)})
        return [self.public_status(i) for i in ids]

    def public_status(self, task_id):
        row = self.status(task_id)
        return {k: v for k, v in row.items() if k != 'spec'}


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '_worker':
        worker(sys.argv[2], sys.argv[3])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    subs = parser.add_subparsers(dest='command', required=True)
    submit = subs.add_parser('submit')
    submit.add_argument('--text', required=True)
    submit.add_argument('--key')
    subs.add_parser('run')
    status = subs.add_parser('status')
    status.add_argument('task_id')
    args = parser.parse_args()
    broker = Broker(args.db)
    if args.command == 'submit':
        value = {'task_id': broker.submit(args.text, args.key)}
    elif args.command == 'run':
        value = asyncio.run(broker.run())
    else:
        value = broker.public_status(args.task_id)
    print(json.dumps(value))


if __name__ == '__main__':
    main()
