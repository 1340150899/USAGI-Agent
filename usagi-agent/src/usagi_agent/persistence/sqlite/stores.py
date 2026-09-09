"""Transactional SQLite Store adapters for the single-node deployment.

Reuse the reference Store state machines, loading and committing their complete
state in one BEGIN IMMEDIATE transaction. This deliberately favors correctness
over throughput; replace with indexed tables before scaling to large tenants.
No object imports or executable deserialization are driven by persisted data.
"""
import asyncio
import base64
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
import hashlib

from pydantic import BaseModel
from usagi_agent.persistence.inmemory import stores, run_lifecycle, accounting, artifact

_MODELS = {}
for module in (stores, run_lifecycle, accounting, artifact):
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            _MODELS[f"{value.__module__}.{value.__name__}"] = value


def encode(value):
    if isinstance(value, BaseModel):
        return ["model", f"{type(value).__module__}.{type(value).__name__}", value.model_dump(mode="json")]
    if isinstance(value, dict):
        return ["dict", [[encode(k), encode(v)] for k, v in value.items()]]
    if isinstance(value, (tuple, list, set)):
        return [type(value).__name__, [encode(v) for v in value]]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode()]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return ["scalar", value]
    raise TypeError(f"unsupported durable value: {type(value)}")


def decode(value):
    kind, *data = value
    if kind == "model":
        return _MODELS[data[0]].model_validate(data[1])
    if kind == "dict":
        return {decode(k): decode(v) for k, v in data[0]}
    if kind in ("list", "tuple", "set"):
        return {"list": list, "tuple": tuple, "set": set}[kind](decode(v) for v in data[0])
    if kind == "bytes":
        return base64.b64decode(data[0])
    if kind == "datetime":
        return datetime.fromisoformat(data[0])
    if kind == "decimal":
        return Decimal(data[0])
    if kind == "scalar":
        return data[0]
    raise ValueError("invalid persisted state tag")


class SqliteStore:
    def __init__(self, path, name, factory):
        self.path, self.name, self.factory = path, name, factory
        self.lock = asyncio.Lock()
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS store_states (name TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def __getattr__(self, name):
        if not callable(getattr(self.factory, name, None)):
            raise AttributeError(name)

        def transaction(args, kwargs):
            db = sqlite3.connect(self.path, timeout=30)
            try:
                db.execute("BEGIN IMMEDIATE")
                target = self.factory()
                row = db.execute("SELECT payload FROM store_states WHERE name=?", (self.name,)).fetchone()
                if row:
                    vars(target).update(decode(json.loads(row[0])))
                result = asyncio.run(getattr(target, name)(*args, **kwargs))
                state = {k: v for k, v in vars(target).items() if k != "_lock"}
                db.execute("INSERT OR REPLACE INTO store_states VALUES (?,?)",
                           (self.name, json.dumps(encode(state))))
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

        async def call(*args, **kwargs):
            async with self.lock:
                return await asyncio.to_thread(transaction, args, kwargs)
        return call


class SqliteBlobStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS artifact_blobs (key TEXT PRIMARY KEY, payload BLOB NOT NULL)")

    async def reserve(self, object_key):
        def operation():
            with sqlite3.connect(self.path, timeout=30) as db:
                db.execute("INSERT OR IGNORE INTO artifact_blobs VALUES (?,?)", (object_key, b""))
        await asyncio.to_thread(operation)

    async def upload(self, object_key, payload):
        data = b"".join([chunk async for chunk in payload])
        def operation():
            with sqlite3.connect(self.path, timeout=30) as db:
                db.execute("INSERT OR REPLACE INTO artifact_blobs VALUES (?,?)", (object_key, data))
        await asyncio.to_thread(operation)

    async def download(self, object_key):
        def operation():
            with sqlite3.connect(self.path, timeout=30) as db:
                return db.execute("SELECT payload FROM artifact_blobs WHERE key=?", (object_key,)).fetchone()
        row = await asyncio.to_thread(operation)
        if row is None:
            raise FileNotFoundError(object_key)
        async def chunks():
            yield row[0]
        return chunks()

    async def delete(self, object_key):
        def operation():
            with sqlite3.connect(self.path, timeout=30) as db:
                db.execute("DELETE FROM artifact_blobs WHERE key=?", (object_key,))
        await asyncio.to_thread(operation)


class SqliteArtifactManager(artifact.InMemoryArtifactManager):
    async def put(self, operation_id, owner, lineage, payload, purpose):
        data=b''.join([chunk async for chunk in payload])
        artifact_id='art_'+hashlib.sha256(json.dumps([owner.tenant_id,owner.erasure_scope_id,operation_id]).encode()).hexdigest()
        existing=await self._meta.get(artifact_id)
        if existing and existing.status=='available':
            stream=await self._blob.download(artifact_id)
            if b''.join([chunk async for chunk in stream])!=data:
                raise ValueError('artifact operation reused with different bytes')
            return artifact.ArtifactRef(artifact_id=artifact_id,content_type=existing.content_type)
        if existing and existing.status not in ('reserved',):
            raise PermissionError('artifact operation is terminal')
        if not existing:
            await self._meta.reserve(artifact_id,owner,operation_id,'application/octet-stream',lineage)
        async def chunks():
            yield data
        await self._blob.upload(artifact_id,chunks())
        await self._meta.finalize(artifact_id,expected_status='reserved')
        return artifact.ArtifactRef(artifact_id=artifact_id,content_type='application/octet-stream')
