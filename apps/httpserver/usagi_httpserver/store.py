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
                  consumed INTEGER DEFAULT 0,
                  status TEXT NOT NULL DEFAULT 'unconsumed', selection_id TEXT,
                  available_since REAL);
                CREATE TABLE IF NOT EXISTS material_windows (
                  id TEXT PRIMARY KEY, principal TEXT, conversation TEXT,
                  status TEXT NOT NULL, session_id TEXT, run_id TEXT,
                  last_seq INTEGER, created REAL, updated REAL);
                CREATE INDEX IF NOT EXISTS ix_material_windows_session
                  ON material_windows(session_id,status);
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
                CREATE TABLE IF NOT EXISTS session_runs (
                  session_id TEXT, run_id TEXT UNIQUE, created REAL,
                  PRIMARY KEY(session_id,run_id));
                CREATE INDEX IF NOT EXISTS ix_session_runs_latest
                  ON session_runs(session_id,created);
                CREATE TABLE IF NOT EXISTS message_sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, message_id TEXT UNIQUE,
                  uptime REAL, crtime REAL);
                CREATE TABLE IF NOT EXISTS delivery_messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT, session_id TEXT,
                  recipient_ref TEXT, fragment_index INTEGER, client_id TEXT, message_id TEXT,
                  status TEXT, uptime REAL, crtime REAL,
                  UNIQUE(delivery_id,client_id));
            ''')
            # Additive migration for databases created before the three-state
            # candidate pool was introduced.
            columns = {row[1] for row in db.execute('PRAGMA table_info(events)')}
            status_added = 'status' not in columns
            if 'status' not in columns:
                db.execute("ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'unconsumed'")
            if 'selection_id' not in columns:
                db.execute('ALTER TABLE events ADD COLUMN selection_id TEXT')
            if 'available_since' not in columns:
                db.execute('ALTER TABLE events ADD COLUMN available_since REAL')
            window_columns = {
                row[1] for row in db.execute('PRAGMA table_info(material_windows)')
            }
            if 'last_seq' not in window_columns:
                db.execute('ALTER TABLE material_windows ADD COLUMN last_seq INTEGER')
            db.execute('''CREATE INDEX IF NOT EXISTS ix_events_candidate_pool
                ON events(status,mode,principal,conversation,seq)''')
            if status_added:
                db.execute("UPDATE events SET status=CASE WHEN consumed=1 THEN 'consumed' ELSE 'unconsumed' END")
            else:
                db.execute("UPDATE events SET status=CASE WHEN consumed=1 THEN 'consumed' ELSE 'unconsumed' END WHERE status IS NULL OR status NOT IN ('unconsumed','selected','consumed')")
            db.execute('UPDATE events SET available_since=received WHERE available_since IS NULL')

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
            now = time.time()
            row = db.execute('''INSERT INTO events(
                event_key,digest,principal,conversation,mode,payload,received,
                consumed,status,selection_id,available_since)
                VALUES (?,?,?,?,?,?,?,0,'unconsumed',NULL,?)''',
                (key, digest, principal, event['conversation_ref'], mode, payload, now, now))
            return row.lastrowid

    def select_material_window(self, *, silence=10, now=None):
        """Atomically select the oldest ready wxauto window.

        A window is ready only after all currently available material has been
        quiet for ``silence`` seconds and the window contains at least one image.
        Marking rows selected moves the next scan's left edge forward.
        """
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            groups = db.execute('''SELECT principal,conversation,
                    MIN(seq) first_seq,MAX(available_since) last_available
                FROM events
                WHERE status='unconsumed' AND mode='material'
                GROUP BY principal,conversation
                ORDER BY first_seq''').fetchall()
            for group in groups:
                if now - group['last_available'] < silence:
                    continue
                rows = db.execute('''SELECT * FROM events
                    WHERE status='unconsumed' AND mode='material'
                      AND principal=? AND conversation=? ORDER BY seq''',
                    (group['principal'], group['conversation'])).fetchall()
                events = [json.loads(row['payload']) for row in rows]
                gap_indexes = [
                    index for index,event in enumerate(events)
                    if event.get('source_gap')
                ]
                if gap_indexes:
                    # A gap is a boundary, not a permanent conversation lock.
                    # Material before it cannot safely be joined to later input,
                    # while a clean suffix after it starts a new sliding window.
                    start = gap_indexes[-1] + 1
                    rows = rows[start:]
                    events = events[start:]
                if not rows:
                    continue
                last_release = db.execute('''SELECT last_seq
                    FROM material_windows
                    WHERE principal=? AND conversation=? AND status='released'
                    ORDER BY updated DESC LIMIT 1''',
                    (group['principal'], group['conversation'])).fetchone()
                if (
                    last_release is not None
                    and last_release['last_seq'] is not None
                    and rows[-1]['seq'] <= last_release['last_seq']
                ):
                    # A returned window is reconsidered only after a newer event
                    # moves the sliding window's right edge.
                    continue
                parts = [part for event in events for part in event['content_parts']]
                if not any(part.get('type') == 'image' for part in parts):
                    continue
                selection_id = 'window_' + uuid.uuid4().hex
                db.executemany(
                    "UPDATE events SET status='selected',selection_id=? WHERE seq=? AND status='unconsumed'",
                    [(selection_id, row['seq']) for row in rows],
                )
                db.execute('''INSERT INTO material_windows(
                    id,principal,conversation,status,last_seq,created,updated)
                    VALUES (?,?,?,'selected',?,?,?)''',
                    (selection_id, group['principal'], group['conversation'],
                     rows[-1]['seq'], now, now),
                )
                text = '\n'.join(
                    part.get('text', '') for part in parts if part['type'] == 'text'
                ).strip()
                return {
                    'id': selection_id,
                    'principal': group['principal'],
                    'payload': {
                        'kind': 'material_window',
                        'query': text,
                        'content_parts': parts,
                        'mode': 'material',
                        'conversation_ref': group['conversation'],
                        'event_range': [rows[0]['seq'], rows[-1]['seq']],
                    },
                }
        return None

    def bind_material_window(self, selection_id, session_id, run_id):
        with self.db() as db:
            db.execute('''UPDATE material_windows
                SET session_id=?,run_id=?,updated=?
                WHERE id=? AND status='selected' ''',
                (session_id, run_id, time.time(), selection_id))

    def material_window_for_session(self, session_id):
        with self.db() as db:
            row = db.execute('''SELECT * FROM material_windows
                WHERE session_id=? AND status='selected'
                ORDER BY created DESC LIMIT 1''', (session_id,)).fetchone()
        return dict(row) if row else None

    def selected_material_windows(self):
        with self.db() as db:
            return [
                dict(row) for row in db.execute('''SELECT * FROM material_windows
                    WHERE status='selected' AND session_id IS NOT NULL
                    ORDER BY created''')
            ]

    def finish_material_window(self, selection_id, *, consumed):
        """Consume a published window or return it to the candidate pool."""
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            status = 'consumed' if consumed else 'unconsumed'
            db.execute('''UPDATE events
                SET status=?,consumed=?,selection_id=NULL,
                    available_since=CASE WHEN ? THEN available_since ELSE ? END
                WHERE selection_id=? AND status='selected' ''',
                (status, int(consumed), int(consumed), now, selection_id))
            db.execute('''UPDATE material_windows SET status=?,updated=?
                WHERE id=? AND status='selected' ''',
                ('consumed' if consumed else 'released', now, selection_id))

    def recover_material_windows(self):
        """Release selections that crashed before a Runtime session was bound."""
        with self.db() as db:
            rows = db.execute("SELECT id FROM material_windows WHERE status='selected' AND session_id IS NULL").fetchall()
        for row in rows:
            self.finish_material_window(row['id'], consumed=False)

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

    def record_session_run(self, session_id, run_id):
        with self.db() as db:
            db.execute('''INSERT OR IGNORE INTO session_runs(session_id,run_id,created)
                VALUES (?,?,?)''', (session_id, run_id, time.time()))

    def latest_run_for_session(self, session_id):
        with self.db() as db:
            row = db.execute('''SELECT run_id FROM session_runs
                WHERE session_id=? ORDER BY created DESC,rowid DESC LIMIT 1''',
                (session_id,)).fetchone()
        return row['run_id'] if row else None

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

    def collect(self, *, silence=10, maximum=900):
        """Create legacy API/interactive jobs; material windows bypass this queue."""
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            groups = db.execute('''SELECT principal,conversation,mode,
                    MIN(received) first,MAX(received) last
                FROM events
                WHERE status='unconsumed' AND mode='interactive'
                GROUP BY principal,conversation,mode''').fetchall()
            for group in groups:
                rows = db.execute("SELECT * FROM events WHERE status='unconsumed' AND principal=? AND conversation=? AND mode=? ORDER BY seq",
                                  (group['principal'], group['conversation'], group['mode'])).fetchall()
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
                if not text:
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
                db.executemany("UPDATE events SET consumed=1,status='consumed' WHERE seq=?", [(r['seq'],) for r in rows])
