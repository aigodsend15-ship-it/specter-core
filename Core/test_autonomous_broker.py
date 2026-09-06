import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from autonomous_broker import Broker, canonical, digest, exclusive


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'fabric.sqlite3'
        self.broker = Broker(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_and_deduplicate(self):
        task = self.broker.submit('Guilherme: ciclo local ✓', 'test')
        asyncio.run(self.broker.run())
        row = self.broker.status(task)
        self.assertEqual(row['state'], 'ATTAINED')
        self.assertEqual(self.broker.submit('Guilherme: ciclo local ✓', 'test'), task)
        self.assertEqual(asyncio.run(self.broker.run()), [])
        with self.broker.connection() as con:
            evidence = con.execute('SELECT manifest,hash FROM broker_evidence').fetchone()
            states = [r[0] for r in con.execute('SELECT state FROM broker_events ORDER BY seq')]
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(con.execute('PRAGMA synchronous').fetchone()[0], 2)
        self.assertEqual(states, ['INIT', 'PLAN', 'EXEC', 'VERIFY', 'ATTAINED'])
        self.assertEqual(digest(b'SPECTER/EVIDENCE/v1\0'+evidence['manifest']), evidence['hash'])
        manifest = json.loads(evidence['manifest'])
        self.assertEqual(digest(Path(manifest['artifact']).read_bytes()), manifest['artifact_sha256'])

    def test_key_conflict(self):
        self.broker.submit('a', 'same')
        with self.assertRaises(ValueError):
            self.broker.submit('b', 'same')

    def test_expired_not_success(self):
        task = self.broker.submit('late')
        with self.broker.connection() as con:
            con.execute('UPDATE broker_tasks SET deadline=0 WHERE id=?', (task,))
        asyncio.run(self.broker.run())
        self.assertEqual(self.broker.status(task)['state'], 'ESCALATE')

    def test_corrupt_output_never_attained(self):
        task = self.broker.submit('expected', max_attempts=2)
        async def corrupt(stage, row):
            if stage == 'VERIFY':
                self.broker.files(task)[1].write_bytes(b'wrong')
        asyncio.run(self.broker.run(corrupt))
        self.assertEqual(self.broker.status(task)['state'], 'ESCALATE')
        with self.broker.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM broker_evidence').fetchone()[0], 0)

    def test_exclusive_dispatch(self):
        self.broker.submit('a')
        with exclusive(self.broker.root / 'dispatcher.lock'):
            with self.assertRaises(OSError):
                asyncio.run(Broker(self.db).run())

    def test_real_process_interruption_and_recovery(self):
        for stage in ['EXEC', 'OUTPUT_COMMITTED', 'VERIFY']:
            with self.subTest(stage=stage):
                task = self.broker.submit(stage, stage)
                marker = Path(self.temp.name) / (stage + '.ready')
                code = (
                    'import asyncio,sys; from pathlib import Path; from autonomous_broker import Broker\n'
                    'async def stop(stage,row):\n'
                    ' if stage==sys.argv[3]:\n'
                    '  Path(sys.argv[2]).write_text(stage)\n'
                    '  await asyncio.sleep(60)\n'
                    'asyncio.run(Broker(Path(sys.argv[1])).run(stop))\n'
                )
                proc = subprocess.Popen([sys.executable, '-c', code, str(self.db), str(marker), stage],
                                        cwd=Path(__file__).parent, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE)
                try:
                    end = time.monotonic()+10
                    while not marker.exists() and time.monotonic() < end and proc.poll() is None:
                        time.sleep(.02)
                    self.assertTrue(marker.exists(), 'child did not reach checkpoint')
                    proc.kill()
                    proc.communicate(timeout=5)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate(timeout=5)
                recovered = Broker(self.db)
                asyncio.run(recovered.run())
                self.assertEqual(recovered.status(task)['state'], 'ATTAINED')
                with recovered.connection() as con:
                    self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(con.execute('SELECT count(*) FROM broker_evidence WHERE task_id=?', (task,)).fetchone()[0], 1)
                    self.assertGreater(con.execute("SELECT count(*) FROM broker_events WHERE task_id=? AND state='RECONCILE'", (task,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
