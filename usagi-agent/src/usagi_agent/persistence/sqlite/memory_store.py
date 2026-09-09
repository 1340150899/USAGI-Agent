"""SQLite BaseStore adapters for short- and long-term memory tables."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from langgraph.store.base import BaseStore, GetOp, Item, ListNamespacesOp, Op, PutOp, Result, SearchItem, SearchOp
from usagi_agent.memory.store import _decode_namespace_label, _matches


class SqliteMemoryLayerStore(BaseStore):
    def __init__(self, path: str | Path, layer: str) -> None:
        self.path, self.layer, self._lock = str(path), layer, threading.RLock()

    def _table(self) -> str:
        return {"raw":"short_term_memory", "short":"short_term_memory",
                "long":"long_term_memory", "tool":"tool_observation_memory"}[self.layer]

    @staticmethod
    def _envelope(namespace, key, value):
        return json.dumps({"namespace":list(namespace),"key":key,"value":value},
                          ensure_ascii=False,separators=(",",":"))

    @staticmethod
    def _decode(row):
        try:
            value=json.loads(row["text"])
            return tuple(value["namespace"]),value["key"],value["value"]
        except (KeyError,TypeError,ValueError,json.JSONDecodeError):
            return None

    def _rows(self, db):
        rows=db.execute(f"SELECT * FROM {self._table()} ORDER BY id").fetchall()
        if self.layer=="raw":
            return [row for row in rows if str(row["type"]).startswith("event:")]
        if self.layer=="short":
            return [row for row in rows if not str(row["type"]).startswith("event:")]
        return rows

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        with self._lock,sqlite3.connect(self.path,timeout=30) as db:
            db.row_factory=sqlite3.Row
            results=[]
            for op in ops:
                decoded=[]
                for row in self._rows(db):
                    item=self._decode(row)
                    if item is not None:decoded.append((row,*item))
                if isinstance(op,GetOp):
                    found=next((entry for entry in decoded if entry[1]==op.namespace and entry[2]==op.key),None)
                    if found is None:results.append(None)
                    else:
                        row,namespace,key,value=found
                        results.append(Item(namespace=namespace,key=key,value=value,
                            created_at=datetime.fromisoformat(row["crtime"]),updated_at=datetime.fromisoformat(row["uptime"])))
                elif isinstance(op,PutOp):
                    matches=[entry for entry in decoded if entry[1]==op.namespace and entry[2]==op.key]
                    table=self._table()
                    if op.value is None:
                        db.executemany(f"DELETE FROM {table} WHERE id=?",[(entry[0]["id"],) for entry in matches])
                    else:
                        now=datetime.now(timezone.utc).isoformat();payload=self._envelope(op.namespace,op.key,op.value)
                        if matches:db.executemany(f"DELETE FROM {table} WHERE id=?",[(entry[0]["id"],) for entry in matches])
                        if self.layer in {"raw","short"}:
                            if self.layer=="raw":row_type="event:"+str(op.value.get("role","unknown"));compressed=0
                            else:row_type="compression" if op.key.startswith("compression:") else "context_state";compressed=int(row_type=="compression")
                            db.execute("INSERT INTO short_term_memory(session_id,text,type,is_compress,uptime,crtime) VALUES (?,?,?,?,?,?)",
                                       (_decode_namespace_label(op.namespace[-1]),payload,row_type,compressed,now,now))
                        else:
                            row_type=None if self.layer=="long" else "tool_observation"
                            db.execute(f"INSERT INTO {table}(uid,text,type,uptime,crtime) VALUES (?,?,?,?,?)",
                                       (_decode_namespace_label(op.namespace[-1]),payload,row_type,now,now))
                    results.append(None)
                elif isinstance(op,SearchOp):
                    terms=set((op.query or "").lower().split());found=[]
                    for row,namespace,key,value in decoded:
                        if namespace[:len(op.namespace_prefix)]!=op.namespace_prefix or not _matches(value,op.filter):continue
                        text=json.dumps(value,ensure_ascii=False).lower()
                        score=sum(term in text for term in terms)/len(terms) if terms else None
                        if terms and score==0:continue
                        found.append(SearchItem(namespace=namespace,key=key,value=value,
                            created_at=datetime.fromisoformat(row["crtime"]),updated_at=datetime.fromisoformat(row["uptime"]),score=score))
                    found.sort(key=lambda item:item.score or 0,reverse=True)
                    results.append(found[op.offset:op.offset+op.limit])
                elif isinstance(op,ListNamespacesOp):
                    namespaces=sorted({entry[1] for entry in decoded})
                    results.append(namespaces[op.offset:op.offset+op.limit])
                else:raise TypeError(f"unsupported LangGraph store operation: {type(op)!r}")
            return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await asyncio.to_thread(self.batch,list(ops))
