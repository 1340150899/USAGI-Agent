"""InMemory FencedCheckpointer.

A *custom* LangGraph ``BaseCheckpointSaver`` (not a decorator). ``aput``/``aput_writes``
verify the live fencing gate (tenant/run/fencing_token/lease owner/expiry/status) before
writing. The gate is injected via ``config["configurable"]["fencing_gate"]`` and checked
through a pluggable async ``gate_verifier`` — keeping the checkpointer decoupled from the
RunControlStore concrete type.

Dev-only: volatile. Recovery promises are delivered by the SQLite backend.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Awaitable, Callable, cast

from langchain_core.runnables import RunnableConfig

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
)

from usagi_agent.api.errors import FencingGateError
from usagi_agent.types.settlement import FencingGate

GateVerifier = Callable[[FencingGate], Awaitable[bool]]


def _cfg(config: RunnableConfig | None, key: str, default: Any = None) -> Any:
    """Read config["configurable"][key]."""
    configurable = (config or {}).get("configurable", {}) or {}
    return configurable.get(key, default)


def _thread_id(config: RunnableConfig | None) -> str:
    value = _cfg(config, "thread_id", default="")
    return value if isinstance(value, str) else ""


def _checkpoint_id(config: RunnableConfig | None) -> str | None:
    value = _cfg(config, "checkpoint_id", default=None)
    return value if isinstance(value, str) else None


def _checkpoint_ns(config: RunnableConfig | None) -> str:
    value = _cfg(config, "checkpoint_ns", default="")
    return value if isinstance(value, str) else ""


def _with_checkpoint_id(
    config: RunnableConfig, checkpoint_id: str, *, checkpoint_ns: str | None = None
) -> RunnableConfig:
    configurable = dict(config.get("configurable", {}))
    configurable["checkpoint_id"] = checkpoint_id
    if checkpoint_ns is not None:
        configurable["checkpoint_ns"] = checkpoint_ns
    return cast(RunnableConfig, {**config, "configurable": configurable})


class InMemoryFencedCheckpointer(BaseCheckpointSaver):
    """Thread-safe in-memory checkpoint storage with an optional fencing gate."""

    def __init__(self, gate_verifier: GateVerifier | None = None) -> None:
        super().__init__()
        self._gate_verifier = gate_verifier
        self._lock = threading.Lock()
        # thread_id -> list of checkpoint entries (oldest first)
        self._checkpoints: dict[str, list[dict[str, Any]]] = {}
        # (thread_id, checkpoint_id, task_id, task_path) -> list[(channel, value)]
        self._writes: dict[tuple[str, str, str, str], list[tuple[str, Any]]] = {}

    # --- gate verification (async only; sync path skips for dev) ---

    async def _verify_gate(self, config: RunnableConfig) -> None:
        if self._gate_verifier is None:
            return
        gate = _cfg(config, "fencing_gate", default=None)
        if gate is None:
            return
        ok = await self._gate_verifier(gate)
        if not ok:
            raise FencingGateError("fencing gate verification failed; cannot write checkpoint")

    # --- sync storage primitives (shared by sync + async API) ---

    def _put_sync(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = _thread_id(config)
        with self._lock:
            latest = self._checkpoints.get(thread_id, [])
            parent_id = latest[-1]["checkpoint_id"] if latest else None
            new_id = "ckpt_" + uuid.uuid4().hex
            entry = {
                "checkpoint_id": new_id,
                "checkpoint": checkpoint,
                "metadata": metadata,
                "parent_checkpoint_id": parent_id,
            }
            latest.append(entry)
            self._checkpoints[thread_id] = latest
            return _with_checkpoint_id(config, new_id)

    def _put_writes_sync(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = _thread_id(config)
        ckpt_id = _checkpoint_id(config) or ""
        key = (thread_id, ckpt_id, task_id, task_path)
        with self._lock:
            self._writes.setdefault(key, []).extend(writes)

    def _get_tuple_sync(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = _thread_id(config)
        ckpt_id = _checkpoint_id(config)
        with self._lock:
            entries = self._checkpoints.get(thread_id, [])
            if not entries:
                return None
            if ckpt_id:
                entry = next((e for e in entries if e["checkpoint_id"] == ckpt_id), None)
            else:
                entry = entries[-1]
            if entry is None:
                return None
            parent_id = entry["parent_checkpoint_id"]
            parent_config = _with_checkpoint_id(config, parent_id) if parent_id else None
            ns = _checkpoint_ns(config)
            pending: list[PendingWrite] = []
            for (tid, cid, tid2, tp), w in self._writes.items():
                if tid == thread_id and cid == entry["checkpoint_id"]:
                    pending.extend((tid2, channel, value) for channel, value in w)
            cfg = _with_checkpoint_id(config, entry["checkpoint_id"], checkpoint_ns=ns)
            return CheckpointTuple(
                config=cfg,
                checkpoint=entry["checkpoint"],
                metadata=entry["metadata"],
                parent_config=parent_config,
                pending_writes=pending,
            )

    def _list_sync(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        config = config or cast(RunnableConfig, {})
        thread_id = _thread_id(config)
        with self._lock:
            entries = list(reversed(self._checkpoints.get(thread_id, [])))
            if before:
                bid = _checkpoint_id(before)
                entries = [e for e in entries if e["checkpoint_id"] != bid] if bid else entries
            if limit:
                entries = entries[:limit]
            for entry in entries:
                cfg = _with_checkpoint_id(config, entry["checkpoint_id"])
                parent_id = entry["parent_checkpoint_id"]
                parent_config = _with_checkpoint_id(config, parent_id) if parent_id else None
                yield CheckpointTuple(
                    config=cfg,
                    checkpoint=entry["checkpoint"],
                    metadata=entry["metadata"],
                    parent_config=parent_config,
                    pending_writes=[],
                )

    # --- async API (used by the async graph runtime) ---

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        await self._verify_gate(config)
        return self._put_sync(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await self._verify_gate(config)
        self._put_writes_sync(config, writes, task_id, task_path)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._get_tuple_sync(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for tup in self._list_sync(config, filter=filter, before=before, limit=limit):
            yield tup

    async def adelete_thread(self, thread_id: str) -> None:
        with self._lock:
            self._checkpoints.pop(thread_id, None)
            self._writes = {k: v for k, v in self._writes.items() if k[0] != thread_id}

    # --- sync API (dev convenience; gate is not enforced on the sync path) ---

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._put_sync(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._put_writes_sync(config, writes, task_id, task_path)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._get_tuple_sync(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        yield from self._list_sync(config, filter=filter, before=before, limit=limit)

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        tup = self._get_tuple_sync(config)
        return tup.checkpoint if tup else None

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            self._checkpoints.pop(thread_id, None)
            self._writes = {k: v for k, v in self._writes.items() if k[0] != thread_id}
