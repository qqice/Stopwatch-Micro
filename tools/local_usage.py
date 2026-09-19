"""Incremental local Codex usage extraction. Never retain prompts/tool payloads."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import sqlite3
import time
from pathlib import Path

MAX_TOKENS = (1 << 53) - 1

def event_from_record(record, previous):
    payload = record.get('payload', {})
    if not isinstance(payload, dict) or record.get('type') != 'event_msg' or payload.get('type') != 'token_count':
        return None, previous
    info = payload.get('info')
    if not isinstance(info, dict): return None, previous
    cumulative, recent = info.get('total_token_usage'), info.get('last_token_usage')
    if not isinstance(cumulative, dict) or not isinstance(recent, dict): return None, previous
    total, last = cumulative.get('total_tokens'), recent.get('total_tokens')
    if any(type(n) is not int or not 0 <= n <= MAX_TOKENS for n in (total, last)):
        return None, previous
    try:
        timestamp = record['timestamp']
        if not isinstance(timestamp, str): return None, previous
        stamp = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if stamp.tzinfo is None: return None, previous
        epoch = int(stamp.timestamp())
    except (KeyError, TypeError, ValueError, OverflowError): return None, previous
    # Equal cumulative totals are repeated notifications, not fresh usage.
    if total == previous: return None, total
    # last_token_usage identifies this completion even when the copied log
    # starts mid-session; do not charge its pre-existing lifetime baseline.
    if not last: return None, total
    identity = json.dumps([timestamp, info.get('total_token_usage'), info.get('last_token_usage')], sort_keys=True, separators=(',', ':'))
    event = {'id': hashlib.sha256(identity.encode()).hexdigest(), 'epoch': epoch,
             'tokens': last}
    return event, total

class LocalUsageScanner:
    def __init__(self, path: Path, home: Path, *, now=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.home, self.now = home, now
        self.db.execute('CREATE TABLE IF NOT EXISTS cursors(path TEXT PRIMARY KEY, offset INTEGER, previous INTEGER, identity TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, epoch INTEGER, tokens INTEGER)')
        self.db.commit()

    def scan(self):
        cutoff = int(self.now()) - 30 * 86400
        for folder in ('sessions', 'archived_sessions'):
            for path in (self.home / folder).rglob('*.jsonl'):
                try:
                    stat = path.stat()
                    if stat.st_mtime < cutoff: continue
                    key = str(path.resolve())
                    identity = f'{stat.st_dev}:{stat.st_ino}'
                    row = self.db.execute('SELECT offset,previous,identity FROM cursors WHERE path=?', (key,)).fetchone()
                    offset, previous = (row[0], row[1]) if row and row[2] == identity and row[0] <= stat.st_size else (0, None)
                    if row and row[2] == identity and offset == stat.st_size: continue
                    with path.open('rb') as f:
                        f.seek(offset)
                        while True:
                            line = f.readline()
                            if not line or not line.endswith(b'\n'): break  # retry partial append next poll
                            offset = f.tell()
                            if b'"token_count"' not in line: continue
                            try: record = json.loads(line)
                            except (ValueError, UnicodeDecodeError): continue
                            if not isinstance(record, dict): continue
                            event, previous = event_from_record(record, previous)
                            if event and cutoff <= event['epoch'] <= int(self.now()) + 300:
                                self.db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?)', (event['id'], event['epoch'], event['tokens']))
                    self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?)', (key, offset, previous, identity))
                    self.db.commit()  # cursor and events are one transaction
                except OSError: continue  # file moved/archived during discovery
        self.db.execute('DELETE FROM outbox WHERE epoch < ?', (cutoff,))
        self.db.commit()

    def pending(self, limit=1000):
        return [dict(zip(('id', 'epoch', 'tokens'), r)) for r in self.db.execute('SELECT id,epoch,tokens FROM outbox ORDER BY epoch LIMIT ?', (limit,))]

    def acknowledge(self, events):
        self.db.executemany('DELETE FROM outbox WHERE id=?', [(e['id'],) for e in events]);self.db.commit()

    def close(self): self.db.close()
