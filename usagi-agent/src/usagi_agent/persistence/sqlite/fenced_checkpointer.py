"""SQLite FencedCheckpointer (design §10.6).

A custom ``BaseCheckpointSaver`` backed by SQLite. ``aput``/``aput_writes`` atomically
verify the fencing gate (tenant/run/fencing_token/lease_owner/DB-clock expiry/status)
inside the *same* transaction that writes the checkpoint — satisfying the single-DB
transaction boundary (§2.3) and the §10.6 contract. ``thread_id == run_id`` (§12.5).
"""
from __future__ import annotations

import pickle
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    WRITES_IDX_MAP,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
)

from usagi_agent.api.errors import FencingGateError
from usagi_agent.persistence.sqlite.schema import apply_schema
from usagi_agent.types.settlement import FencingGate


def _cfg(config: RunnableConfig | None, key: str, default: Any = None) -> Any:
    return ((config or {}).get("configurable") or {}).get(key, default)


def _gate(config: RunnableConfig) -> FencingGate | None:
    value = _cfg(config, "fencing_gate")
    return value if isinstance(value, FencingGate) else None


def _with_checkpoint_id(config: RunnableConfig, checkpoint_id: str) -> RunnableConfig:
    configurable = dict(config.get("configurable", {}))
    configurable["checkpoint_id"] = checkpoint_id
    return cast(RunnableConfig, {**config, "configurable": configurable})


class SqliteFencedCheckpointer(BaseCheckpointSaver):
    def __init__(self, db_path: str = ":memory:") -> None:
        super().__init__()
        self._db_path = db_path
        import sqlite3

        # Apply schema eagerly on a sync connection (init-time only).
        conn = sqlite3.connect(db_path)
        try:
            apply_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        import aiosqlite

        return aiosqlite.connect(self._db_path)

    async def abind_thread(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        control_id: str,
        graph_checksum: str,
    ) -> None:
        async with self._connect() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO thread_control_bindings "
                "(tenant_id, thread_id, control_kind, control_id, graph_checksum) "
                "VALUES (?, ?, 'run', ?, ?)",
                (tenant_id, thread_id, control_id, graph_checksum),
            )
            cur = await conn.execute(
                "SELECT tenant_id, control_kind, control_id, graph_checksum "
                "FROM thread_control_bindings WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row != (tenant_id, "run", control_id, graph_checksum):
                raise FencingGateError("thread is bound to different runtime control")
            await conn.commit()

    @staticmethod
    def _verify_gate_sql() -> str:
        return (
            "SELECT 1 FROM thread_control_bindings AS binding "
            "JOIN run_controls AS control "
            "ON control.tenant_id = binding.tenant_id "
            "AND control.run_id = binding.control_id "
            "WHERE binding.tenant_id = :tenant_id "
            "AND binding.thread_id = :thread_id "
            "AND binding.control_kind = 'run' "
            "AND binding.control_id = :run_id "
            "AND control.fencing_token = :fencing_token "
            "AND control.lease_owner = :lease_owner "
            "AND control.lease_expires_at > datetime('now') "
            "AND control.run_status IN ('running','resume_accepted')"
        )

    async def _verify_gate(self, conn, gate: FencingGate, thread_id: str) -> None:
        cur = await conn.execute(self._verify_gate_sql(), {
            "tenant_id": gate.tenant_id, "run_id": gate.control_id,
            # LangGraph appends ``|checkpoint_ns`` to the configured thread id.
            "thread_id": thread_id.partition("|")[0],
            "fencing_token": gate.fencing_token, "lease_owner": gate.lease_owner,
        })
        row = await cur.fetchone()
        await cur.close()
        if not row:
            raise FencingGateError("sqlite fencing gate verification failed")

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        gate = _gate(config)
        thread_id = (_cfg(config, "thread_id", "") + "|" + _cfg(config, "checkpoint_ns", ""))
        tenant_id = _cfg(config, "tenant_id", "default")
        new_id = checkpoint["id"]
        parent_id = _cfg(config, "checkpoint_id")
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            if gate is not None:
                await self._verify_gate(conn, gate, thread_id)
            await conn.execute(
                "INSERT OR REPLACE INTO checkpoints (tenant_id, thread_id, checkpoint_id, parent_checkpoint_id, checkpoint, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, thread_id, new_id, parent_id,
                 pickle.dumps(checkpoint), pickle.dumps(metadata),
                 datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()
        return _with_checkpoint_id(config, new_id)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        gate = _gate(config)
        thread_id = (_cfg(config, "thread_id", "") + "|" + _cfg(config, "checkpoint_ns", ""))
        tenant_id = _cfg(config, "tenant_id", "default")
        checkpoint_id = _cfg(config, "checkpoint_id", "")
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            if gate is not None:
                await self._verify_gate(conn, gate, thread_id)
            for idx, (channel, value) in enumerate(writes):
                await conn.execute(
                    ("INSERT OR REPLACE" if channel in WRITES_IDX_MAP else "INSERT OR IGNORE") + " INTO checkpoint_writes (tenant_id, thread_id, checkpoint_id, task_id, task_path, channel, value, idx) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (tenant_id, thread_id, checkpoint_id, task_id, task_path,
                     channel, pickle.dumps(value), WRITES_IDX_MAP.get(channel, idx)),
                )
            await conn.commit()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = (_cfg(config, "thread_id", "") + "|" + _cfg(config, "checkpoint_ns", ""))
        tenant_id = _cfg(config, "tenant_id", "default")
        checkpoint_id = _cfg(config, "checkpoint_id")
        async with self._connect() as conn:
            if checkpoint_id:
                cur = await conn.execute(
                    "SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata FROM checkpoints "
                    "WHERE tenant_id = ? AND thread_id = ? AND checkpoint_id = ?",
                    (tenant_id, thread_id, checkpoint_id),
                )
            else:
                cur = await conn.execute(
                    "SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata FROM checkpoints "
                    "WHERE tenant_id = ? AND thread_id = ? ORDER BY created_at DESC LIMIT 1",
                    (tenant_id, thread_id),
                )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                return None
            ckpt_id, parent_id, blob, meta_blob = row
            wcur = await conn.execute(
                "SELECT task_id, channel, value FROM checkpoint_writes "
                "WHERE tenant_id = ? AND thread_id = ? AND checkpoint_id = ?",
                (tenant_id, thread_id, ckpt_id),
            )
            pending: list[PendingWrite] = [
                (task_id, channel, pickle.loads(value))
                for task_id, channel, value in await wcur.fetchall()
            ]
            await wcur.close()
            parent_config = _with_checkpoint_id(config, parent_id) if parent_id else None
            cfg = _with_checkpoint_id(config, ckpt_id)
            return CheckpointTuple(
                config=cfg, checkpoint=pickle.loads(blob), metadata=pickle.loads(meta_blob),
                parent_config=parent_config, pending_writes=pending,
            )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        config = config or cast(RunnableConfig, {})
        thread_id = (_cfg(config, "thread_id", "") + "|" + _cfg(config, "checkpoint_ns", ""))
        tenant_id = _cfg(config, "tenant_id", "default")
        async with self._connect() as conn:
            cur = await conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata FROM checkpoints "
                "WHERE tenant_id = ? AND thread_id = ? ORDER BY created_at DESC",
                (tenant_id, thread_id),
            )
            rows = list(await cur.fetchall())
            await cur.close()
        if limit:
            rows = rows[:limit]
        for ckpt_id, parent_id, blob, meta_blob in rows:
            cfg = _with_checkpoint_id(config, ckpt_id)
            parent_config = _with_checkpoint_id(config, parent_id) if parent_id else None
            yield CheckpointTuple(
                config=cfg, checkpoint=pickle.loads(blob), metadata=pickle.loads(meta_blob),
                parent_config=parent_config, pending_writes=[],
            )

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._connect() as conn:
            prefix = thread_id + "|"
            await conn.execute("DELETE FROM checkpoints WHERE thread_id = ? OR substr(thread_id,1,?) = ?", (thread_id,len(prefix),prefix))
            await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = ? OR substr(thread_id,1,?) = ?", (thread_id,len(prefix),prefix))
            await conn.commit()

    # Sync API: delegate via a throwaway loop is unsafe in async context; raise clearly.
    def put(self, *a, **k):  # noqa: D401
        raise NotImplementedError("SqliteFencedCheckpointer is async-only; use the async graph runtime.")

    def put_writes(self, *a, **k):
        raise NotImplementedError("SqliteFencedCheckpointer is async-only.")

    def get_tuple(self, config):
        raise NotImplementedError("SqliteFencedCheckpointer is async-only.")

    def list(self, config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("SqliteFencedCheckpointer is async-only.")

    def get(self, config):
        raise NotImplementedError("SqliteFencedCheckpointer is async-only.")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("SqliteFencedCheckpointer is async-only.")
