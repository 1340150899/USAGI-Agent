import json
import sqlite3
import uuid
from datetime import datetime, timezone


def overlap(previous, current):
    """Return the observed prefix length; a missing anchor is a gap, never a guess."""
    if not previous:
        return 0
    if previous==current:
        return len(current)
    for size in range(min(len(previous),len(current)),0,-1):
        if previous[-size:]==current[:size]:
            anchor=current[:size]
            if sum(current[i:i+size]==anchor for i in range(len(current)-size+1))!=1:
                return None
            return size
    return None


class Spool:
    def __init__(self,path):
        self.path=str(path)
        with sqlite3.connect(self.path) as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS snapshots(conversation TEXT PRIMARY KEY,payload TEXT,epoch TEXT,seq INTEGER,blocked INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT,status TEXT);''')

    def capture(self,config,conversation,messages,*,rebaseline=False):
        signatures=[m['signature'] for m in messages]
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload,epoch,seq,blocked FROM snapshots WHERE conversation=?',(conversation['ref'],)).fetchone()
            if old is None or rebaseline:
                db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,0)',
                           (conversation['ref'],json.dumps(signatures),uuid.uuid4().hex,0))
                return
            if old[3]:return
            start=overlap(json.loads(old[0]),signatures)
            epoch,seq=old[1],old[2]+1
            gap=start is None
            selected=[{'text':'采集窗口失去连续观察锚点，请人工核对并重新建立基线。','sender_ref':conversation['sender_ref'],'direction':'incoming'}] if gap else messages[start:]
            for index,msg in enumerate(selected):
                event_id=f'{epoch}:{seq}:{index}'
                payload={'schema_version':1,'source_stream_id':config['account_ref']+':'+conversation['ref'],
                    'source_event_ref':event_id,'source_cursor_ref':event_id,'ordering_mode':'observation_ordered',
                    'account_ref':config['account_ref'],'conversation_ref':conversation['ref'],
                    'sender_ref':msg['sender_ref'],'direction':msg['direction'],
                    'occurred_at':datetime.now(timezone.utc).isoformat(),'source_gap':gap,
                    'content_parts':[{'type':'text','text':msg.get('text','')}],
                    '_image_path':msg.get('image_path')}
                db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)',(event_id,json.dumps(payload,ensure_ascii=False),'pending'))
            db.execute('UPDATE snapshots SET payload=?,seq=?,blocked=? WHERE conversation=?',
                       (json.dumps(signatures),seq,int(gap),conversation['ref']))

    def pending(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT id,payload FROM events WHERE status='pending' ORDER BY rowid LIMIT 20").fetchall()

    def ack(self,event_id):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE events SET status='accepted' WHERE id=?",(event_id,))
