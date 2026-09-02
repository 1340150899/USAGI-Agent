"""A small JSON-file implementation of LangGraph's BaseStore interface."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)


def _matches(value: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for path, expected in filters.items():
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if current != expected:
            return False
    return True


class JsonFileStore(BaseStore):
    """Local V1 store with the same API used by LangGraph database stores.

    Writes are atomic (temporary file + replace). Search is lexical because V1 has no
    embedding service; swapping this object for a DB/vector BaseStore needs no manager
    or pipeline changes.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: dict[tuple[tuple[str, ...], str], Item] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for row in payload.get("items", []):
            item = Item(
                namespace=tuple(row["namespace"]),
                key=row["key"],
                value=row["value"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            self._items[(item.namespace, item.key)] = item

    def _flush(self) -> None:
        rows = [
            {
                "namespace": list(item.namespace),
                "key": item.key,
                "value": item.value,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in self._items.values()
        ]
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"items": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        with self._lock:
            results: list[Result] = []
            dirty = False
            for op in ops:
                if isinstance(op, GetOp):
                    results.append(self._items.get((op.namespace, op.key)))
                elif isinstance(op, PutOp):
                    identity = (op.namespace, op.key)
                    if op.value is None:
                        dirty = self._items.pop(identity, None) is not None or dirty
                    else:
                        now = datetime.now(timezone.utc)
                        previous = self._items.get(identity)
                        self._items[identity] = Item(
                            namespace=op.namespace,
                            key=op.key,
                            value=op.value,
                            created_at=previous.created_at if previous else now,
                            updated_at=now,
                        )
                        dirty = True
                    results.append(None)
                elif isinstance(op, SearchOp):
                    query_terms = set((op.query or "").lower().split())
                    found: list[SearchItem] = []
                    for item in self._items.values():
                        if item.namespace[: len(op.namespace_prefix)] != op.namespace_prefix:
                            continue
                        if not _matches(item.value, op.filter):
                            continue
                        text = json.dumps(item.value, ensure_ascii=False).lower()
                        score = (
                            sum(1 for term in query_terms if term in text) / len(query_terms)
                            if query_terms
                            else None
                        )
                        if query_terms and score == 0:
                            continue
                        found.append(SearchItem(**item.dict(), score=score))
                    found.sort(key=lambda value: value.score or 0, reverse=True)
                    results.append(found[op.offset : op.offset + op.limit])
                elif isinstance(op, ListNamespacesOp):
                    namespaces = sorted({item.namespace for item in self._items.values()})
                    for condition in op.match_conditions or ():
                        path = tuple(condition.path)
                        if condition.match_type == "prefix":
                            namespaces = [n for n in namespaces if n[: len(path)] == path]
                        else:
                            namespaces = [n for n in namespaces if n[-len(path) :] == path]
                    if op.max_depth is not None:
                        namespaces = sorted({n[: op.max_depth] for n in namespaces})
                    results.append(namespaces[op.offset : op.offset + op.limit])
                else:
                    raise TypeError(f"unsupported LangGraph store operation: {type(op)!r}")
            if dirty:
                self._flush()
            return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await asyncio.to_thread(self.batch, list(ops))

