"""Application inbox, immutable work items, media ownership and delivery outbox."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AppStore:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE, digest TEXT,
                  principal TEXT, conversation TEXT, mode TEXT, payload TEXT, received REAL,
                  consumed INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, principal TEXT, payload TEXT, status TEXT,
                  run_id TEXT, outcome TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS deliveries (
                  id TEXT PRIMARY KEY, principal TEXT, payload TEXT, status TEXT,
                  next_attempt REAL DEFAULT 0, attempts INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS media (
                  id TEXT PRIMARY KEY, principal TEXT, path TEXT, mime TEXT, sha256 TEXT);
                CREATE TABLE IF NOT EXISTS heartbeat (
                  source TEXT PRIMARY KEY, payload TEXT, seen REAL);
                CREATE TABLE IF NOT EXISTS session_routes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, session_id TEXT UNIQUE,
                  uptime REAL, crtime REAL);
                CREATE INDEX IF NOT EXISTS ix_session_routes_uid ON session_routes(uid,crtime);
                CREATE TABLE IF NOT EXISTS message_sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, message_id TEXT UNIQUE,
                  uptime REAL, crtime REAL);
                CREATE TABLE IF NOT EXISTS delivery_messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT, session_id TEXT,
                  recipient_ref TEXT, fragment_index INTEGER, client_id TEXT, message_id TEXT,
                  status TEXT, uptime REAL, crtime REAL,
                  UNIQUE(delivery_id,client_id));
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def accept(self, principal, source, mode, event):
        key = canonical([source, event['account_ref'], event['source_stream_id'], event['source_event_ref']])
        payload = canonical(event)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT seq,digest,principal FROM events WHERE event_key=?', (key,)).fetchone()
            if existing:
                if existing['digest'] != digest or existing['principal'] != principal:
                    raise ValueError('event id reused with different content')
                return existing['seq']
            row = db.execute('INSERT INTO events(event_key,digest,principal,conversation,mode,payload,received) VALUES (?,?,?,?,?,?,?)',
                (key, digest, principal, event['conversation_ref'], mode, payload, time.time()))
            return row.lastrowid

    def enqueue(self, principal, key, payload):
        job_id = hashlib.sha256(canonical([principal, key]).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if existing and existing['payload'] != canonical(payload):
                raise ValueError('idempotency key reused with different request')
            db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?, ?,NULL,NULL,?)',
                       (job_id, principal, canonical(payload), 'queued', time.time()))
        return job_id

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (row['id'],))
                return dict(row)

    def finish(self, job_id, status, run_id=None, outcome=None):
        with self.db() as db:
            db.execute('UPDATE jobs SET status=?,run_id=COALESCE(?,run_id),outcome=? WHERE id=?',
                       (status, run_id, canonical(outcome), job_id))
            if run_id and status!='suspended':
                db.execute("UPDATE jobs SET status=?,outcome=? WHERE run_id=? AND status='suspended'",
                           (status,canonical(outcome),run_id))

    def recover(self):
        # Kernel idempotency determines whether a start already ran. Resume handling
        # checks current approval/run state before any attempt to invoke it again.
        with self.db() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def job(self, job_id, principal):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND principal=?', (job_id, principal)).fetchone()
        return dict(row) if row else None

    def notify(self, delivery_id, principal, payload):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO deliveries(id,principal,payload,status) VALUES (?,?,?,?)',
                       (delivery_id, principal, canonical(payload), 'queued'))

    def pending_deliveries(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM deliveries WHERE (status IN ('queued','accepted','sending') OR (status='sent' AND attempts<120)) AND next_attempt<=?", (time.time(),))]

    def delivery_status(self, delivery_id, status):
        with self.db() as db:
            db.execute('UPDATE deliveries SET status=?,attempts=attempts+1,next_attempt=? WHERE id=?',
                       (status, time.time()+10, delivery_id))

    def bind_session(self, uid, session_id):
        now=time.time()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO session_routes(uid,session_id,uptime,crtime) VALUES (?,?,?,?)',
                       (uid,session_id,now,now))
            db.execute('UPDATE session_routes SET uptime=? WHERE session_id=?',(now,session_id))

    def session_for_uid(self, uid):
        with self.db() as db:
            row=db.execute('SELECT session_id FROM session_routes WHERE uid=? ORDER BY crtime DESC LIMIT 1',(uid,)).fetchone()
        return row['session_id'] if row else None

    def session_for_message(self, message_id):
        with self.db() as db:
            row=db.execute('SELECT session_id FROM message_sessions WHERE message_id=?',(str(message_id),)).fetchone()
        return row['session_id'] if row else None

    def record_delivery_messages(self, delivery_id, session_id, messages, status):
        now=time.time()
        with self.db() as db:
            for item in messages:
                client_id=item.get('client_id')
                if not client_id:
                    continue
                message_id=item.get('item_msg_id') or item.get('message_id')
                db.execute('''INSERT INTO delivery_messages(
                    delivery_id,session_id,recipient_ref,fragment_index,client_id,message_id,status,uptime,crtime)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(delivery_id,client_id) DO UPDATE SET
                    message_id=excluded.message_id,status=excluded.status,uptime=excluded.uptime''',
                    (delivery_id,session_id,item.get('recipient_ref'),item.get('fragment_index'),
                     client_id,str(message_id) if message_id is not None else None,status,now,now))
                if message_id is not None:
                    db.execute('INSERT OR REPLACE INTO message_sessions(session_id,message_id,uptime,crtime) VALUES (?,?,?,?)',
                               (session_id,str(message_id),now,now))
        return bool(messages) and all(item.get('item_msg_id') or item.get('message_id') for item in messages)

    def media(self, media_id, principal):
        with self.db() as db:
            row = db.execute('SELECT * FROM media WHERE id=? AND principal=?', (media_id, principal)).fetchone()
        return dict(row) if row else None

    def collect(self, *, silence=90, maximum=900):
        """Freeze ready input groups and create jobs in the same transaction."""
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            groups = db.execute('SELECT principal,conversation,mode,MIN(received) first,MAX(received) last FROM events WHERE consumed=0 GROUP BY principal,conversation,mode').fetchall()
            for group in groups:
                rows = db.execute('SELECT * FROM events WHERE consumed=0 AND principal=? AND conversation=? AND mode=? ORDER BY seq',
                                  (group['principal'], group['conversation'], group['mode'])).fetchall()
                if group['mode']=='interactive':
                    # Process each textual turn separately, preserving preceding images.
                    # Session and approval semantics are owned by the agent framework.
                    for index,row in enumerate(rows):
                        message=json.loads(row['payload'])
                        message_text='\n'.join(p.get('text','') for p in message['content_parts'] if p['type']=='text').strip()
                        if message_text:
                            rows=rows[:index+1]
                            break
                events = [json.loads(r['payload']) for r in rows]
                text = '\n'.join(p.get('text','') for e in events for p in e['content_parts'] if p['type']=='text').strip()
                if group['mode']=='material':
                    if now-group['last'] < silence and now-group['first'] < maximum:
                        continue
                    if any(e.get('source_gap') for e in events):
                        continue
                elif not text:
                    continue  # Images wait for the user's task/"start generation" text.
                parts = [p for e in events for p in e['content_parts']]
                payload = {'kind':'message', 'query':text, 'content_parts':parts,
                           'mode':group['mode'], 'conversation_ref':group['conversation'],
                           'reply_route_ref':events[-1].get('reply_route_ref'),
                           'uid':events[-1].get('uid') or group['principal'],
                           'message_id':events[-1].get('message_id'),
                           'ref_msg_id':events[-1].get('ref_msg_id'),
                           'event_range':[rows[0]['seq'],rows[-1]['seq']]}
                job_id = 'events_' + str(rows[0]['seq']) + '_' + str(rows[-1]['seq'])
                db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,NULL,NULL,?)',
                           (job_id, group['principal'], canonical(payload), 'queued', now))
                db.executemany('UPDATE events SET consumed=1 WHERE seq=?', [(r['seq'],) for r in rows])
