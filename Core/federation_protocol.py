"""SPECTER federation/1: local, durable proposal mailbox; never executes payloads.

The caller supplies the authenticated sender from a trusted local adapter.
This module is not a network authentication boundary or an execution grant.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

PROTOCOL = 'specter/federation/1'
FIELDS = {'protocol', 'id', 'sender', 'recipient', 'kind', 'objective',
          'created_at', 'expires_at', 'payload'}
KINDS = {'hello', 'proposal', 'result', 'meeting', 'cancel'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False)


def validate(message, sender, members, now=None):
    now = int(time.time()) if now is None else now
    if not isinstance(message, dict) or set(message) != FIELDS:
        raise ValueError('Unexpected envelope fields')
    if message['protocol'] != PROTOCOL:
        raise ValueError('Unsupported protocol')
    for field in ('sender', 'recipient'):
        value = message[field]
        if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', value):
            raise ValueError('Invalid member name')
        if value not in members:
            raise ValueError('Unknown member')
    if message['sender'] != sender:
        raise ValueError('Sender does not match trusted adapter')
    try:
        if str(uuid.UUID(message['id'])) != message['id']:
            raise ValueError('Noncanonical id')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Expected canonical UUID') from None
    if not isinstance(message['kind'], str) or message['kind'] not in KINDS:
        raise ValueError('Unsupported kind')
    if not isinstance(message['objective'], str) or not 1 <= len(message['objective']) <= 2000:
        raise ValueError('Invalid objective')
    start, end = message['created_at'], message['expires_at']
    if type(start) is not int or type(end) is not int:
        raise ValueError('Timestamps must be integer epoch seconds')
    if start > now + 60 or end <= now or not 0 < end - start <= 86400:
        raise ValueError('Expired or invalid lifetime (maximum 24 hours)')
    if not isinstance(message['payload'], dict):
        raise ValueError('Payload must be an object')
    raw = canonical(message)
    if len(raw.encode('ascii')) > 32768:
        raise ValueError('Envelope exceeds 32 KiB')
    return raw


class Mailbox:
    """At-least-once polling with recipient acknowledgement and immutable IDs."""
    def __init__(self, path, members):
        self.path = Path(path)
        self.members = frozenset(members)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS federation_messages (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, recipient TEXT NOT NULL,
                envelope TEXT NOT NULL, expires INTEGER NOT NULL, acked INTEGER NOT NULL DEFAULT 0)''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def submit(self, message, authenticated_sender, now=None):
        raw = validate(message, authenticated_sender, self.members, now)
        digest = hashlib.sha256(raw.encode('ascii')).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest FROM federation_messages WHERE id=?', (message['id'],)).fetchone()
            if old and old[0] != digest:
                raise ValueError('ID reused with different contents')
            if not old:
                db.execute('INSERT INTO federation_messages VALUES (?,?,?,?,?,0)',
                           (message['id'], digest, message['recipient'], raw, message['expires_at']))
        return {'id': message['id'], 'sha256': digest, 'duplicate': bool(old), 'status': 'stored'}

    def poll(self, authenticated_recipient, now=None, limit=50):
        if authenticated_recipient not in self.members:
            raise ValueError('Unknown recipient')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Limit must be 1..100')
        now = int(time.time()) if now is None else now
        with closing(self.connect()) as db:
            rows = db.execute('''SELECT envelope FROM federation_messages
                WHERE recipient=? AND acked=0 AND expires>? ORDER BY rowid LIMIT ?''',
                (authenticated_recipient, now, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, message_id, authenticated_recipient):
        if authenticated_recipient not in self.members:
            raise ValueError('Unknown recipient')
        with closing(self.connect()) as db, db:
            cursor = db.execute('UPDATE federation_messages SET acked=1 WHERE id=? AND recipient=?',
                                (message_id, authenticated_recipient))
            if cursor.rowcount != 1:
                raise ValueError('Message not found for recipient')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['submit', 'poll', 'ack'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--actor', required=True, help='Local operator identity; not remote authentication')
    parser.add_argument('--members', default='codex,chatgpt,hermes,opencode')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--id')
    args = parser.parse_args()
    mailbox = Mailbox(args.db, args.members.split(','))
    if args.action == 'submit':
        if not args.file or args.file.stat().st_size > 32768:
            parser.error('submit requires --file up to 32 KiB')
        result = mailbox.submit(json.loads(args.file.read_text(encoding='utf-8-sig')), args.actor)
    elif args.action == 'poll':
        result = mailbox.poll(args.actor)
    else:
        if not args.id:
            parser.error('ack requires --id')
        mailbox.acknowledge(args.id, args.actor)
        result = {'id': args.id, 'status': 'acknowledged'}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
