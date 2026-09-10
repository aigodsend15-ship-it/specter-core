import concurrent.futures
import tempfile
import unittest
import uuid
from pathlib import Path
from Core.federation_protocol import Mailbox, PROTOCOL


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = Mailbox(Path(self.tmp.name) / 'mail.sqlite3', {'codex', 'hermes'})
        self.msg = dict(protocol=PROTOCOL, id=str(uuid.uuid4()), sender='codex',
                        recipient='hermes', kind='proposal', objective='Review only',
                        created_at=100, expires_at=200, payload={'text': 'untrusted data'})

    def test_roundtrip_and_recipient_isolation(self):
        self.box.submit(self.msg, 'codex', now=110)
        self.assertEqual(self.box.poll('codex', now=110), [])
        self.assertEqual(self.box.poll('hermes', now=110), [self.msg])
        with self.assertRaises(ValueError):
            self.box.acknowledge(self.msg['id'], 'codex')
        self.box.acknowledge(self.msg['id'], 'hermes')
        self.assertEqual(self.box.poll('hermes', now=110), [])

    def test_concurrent_duplicate_and_conflict(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: self.box.submit(self.msg, 'codex', now=110), range(8)))
        self.assertEqual(sum(not r['duplicate'] for r in receipts), 1)
        changed = dict(self.msg, payload={'text': 'changed'})
        with self.assertRaises(ValueError):
            self.box.submit(changed, 'codex', now=110)

    def test_reject_spoof_expiry_unknown_and_oversize(self):
        for patch in ({'sender': 'hermes'}, {'recipient': 'intruder'}, {'kind': 'exec'},
                      {'expires_at': 110}, {'created_at': True}, {'grant': 'admin'},
                      {'payload': {'text': 'x' * 40000}}, {'payload': {'bad': float('nan')}}):
            with self.subTest(patch=list(patch)), self.assertRaises(ValueError):
                self.box.submit(dict(self.msg, **patch), 'codex', now=110)

    def test_expiry_and_durable_reopen(self):
        self.box.submit(self.msg, 'codex', now=110)
        reopened = Mailbox(self.box.path, {'codex', 'hermes'})
        self.assertEqual(reopened.poll('hermes', now=199), [self.msg])
        self.assertEqual(reopened.poll('hermes', now=200), [])


if __name__ == '__main__':
    unittest.main()
