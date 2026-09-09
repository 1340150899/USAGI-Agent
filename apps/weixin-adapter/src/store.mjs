import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';

export const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export class Store {
  constructor(path) {
    this.db=new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,payload TEXT,status TEXT);
      CREATE TABLE IF NOT EXISTS bound_users(
        uid TEXT PRIMARY KEY,uptime REAL NOT NULL,crtime REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS outgoing(
        id TEXT PRIMARY KEY,payload TEXT,status TEXT,messages TEXT DEFAULT '[]');`);
  }
  recover() {this.db.prepare("UPDATE outgoing SET status='unknown' WHERE status='sending'").run();}
  bindUser(uid) {
    if(!uid)return;
    const now=Date.now();
    this.db.prepare(`INSERT INTO bound_users(uid,uptime,crtime) VALUES (?,?,?)
      ON CONFLICT(uid) DO UPDATE SET uptime=excluded.uptime`).run(uid,now,now);
  }
  boundUsers() {return this.db.prepare('SELECT uid,uptime,crtime FROM bound_users ORDER BY crtime').all();}
  get(key,fallback=null) { const r=this.db.prepare('SELECT value FROM kv WHERE key=?').get(key);return r?JSON.parse(r.value):fallback; }
  set(key,value) {this.db.prepare('INSERT OR REPLACE INTO kv VALUES (?,?)').run(key,JSON.stringify(value));}
  batch(account,response) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const batch=digest([account,this.get('cursor:'+account,'')]);
      for(const [index,msg] of (response.msgs??[]).entries()) {
        const id=digest([account,msg.message_id ?? [batch,index]]);
        this.db.prepare('INSERT OR IGNORE INTO inbox VALUES (?,?,?)').run(id,JSON.stringify({account,msg}),'pending');
      }
      if(response.get_updates_buf!==undefined)this.set('cursor:'+account,response.get_updates_buf);
      this.db.exec('COMMIT');
    } catch(error) {this.db.exec('ROLLBACK');throw error;}
  }
  pending() {return this.db.prepare("SELECT * FROM inbox WHERE status='pending' ORDER BY rowid LIMIT 20").all();}
  ack(id) {this.db.prepare("UPDATE inbox SET status='accepted' WHERE id=?").run(id);}
  outgoing(id,payload) {
    const existing=this.db.prepare('SELECT * FROM outgoing WHERE id=?').get(id);
    if(existing) {
      if(existing.payload!==JSON.stringify(payload))throw new Error('delivery conflict');
      return existing.status;
    }
    this.db.prepare('INSERT INTO outgoing(id,payload,status,messages) VALUES (?,?,?,?)')
      .run(id,JSON.stringify(payload),'accepted','[]');return 'accepted';
  }
  queued() {return this.db.prepare("SELECT * FROM outgoing WHERE status='accepted' ORDER BY rowid LIMIT 10").all();}
  claim(id) {return this.db.prepare("UPDATE outgoing SET status='sending' WHERE id=? AND status='accepted'").run(id).changes===1;}
  status(id,status) {this.db.prepare('UPDATE outgoing SET status=? WHERE id=?').run(status,id);}
  sent(id,message) {
    const row=this.db.prepare('SELECT messages FROM outgoing WHERE id=?').get(id);
    if(!row)throw new Error('delivery not found');
    const messages=JSON.parse(row.messages??'[]');
    const index=messages.findIndex(item=>item.client_id===message.client_id);
    if(index>=0)messages[index]={...messages[index],...message};else messages.push(message);
    this.db.prepare('UPDATE outgoing SET messages=? WHERE id=?')
      .run(JSON.stringify(messages),id);
  }
  delivery(id) {
    const row=this.db.prepare('SELECT id,status,messages FROM outgoing WHERE id=?').get(id);
    return row?{id:row.id,status:row.status,messages:JSON.parse(row.messages??'[]'),
      client_ids:JSON.parse(row.messages??'[]').map(item=>item.client_id).filter(Boolean),
      message_ids:JSON.parse(row.messages??'[]').flatMap(item=>[item.item_msg_id,item.message_id]).filter(Boolean)}:undefined;
  }
  confirm(clientId,confirmation) {
    for(const row of this.db.prepare("SELECT id,messages FROM outgoing WHERE messages LIKE ?").all('%'+clientId+'%')) {
      const messages=JSON.parse(row.messages??'[]');
      const item=messages.find(value=>value.client_id===clientId);
      if(item) {
        if(confirmation.message_id!==undefined)item.message_id=String(confirmation.message_id);
        if(confirmation.item_msg_id!==undefined)item.item_msg_id=String(confirmation.item_msg_id);
        this.db.prepare('UPDATE outgoing SET messages=? WHERE id=?')
          .run(JSON.stringify(messages),row.id);
      }
    }
  }
  routes() {
    return this.db.prepare("SELECT key,value FROM kv WHERE key LIKE 'route:%'").all()
      .map(row=>({routeRef:row.key.slice(6),...JSON.parse(row.value)}));
  }
}
