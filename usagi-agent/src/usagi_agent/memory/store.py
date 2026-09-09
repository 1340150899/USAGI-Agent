"""SQLite-backed memory store bundle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.store.base import BaseStore


@dataclass(frozen=True)
class MemoryStores:
    raw_conversations: BaseStore
    short_term: BaseStore
    long_term: BaseStore
    tool_observations: BaseStore

    @classmethod
    def sqlite(cls, path: str | Path) -> "MemoryStores":
        import sqlite3

        from usagi_agent.persistence.sqlite.memory_store import SqliteMemoryLayerStore
        from usagi_agent.persistence.sqlite.schema import apply_schema

        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            apply_schema(connection)
        return cls(
            raw_conversations=SqliteMemoryLayerStore(database_path, "raw"),
            short_term=SqliteMemoryLayerStore(database_path, "short"),
            long_term=SqliteMemoryLayerStore(database_path, "long"),
            tool_observations=SqliteMemoryLayerStore(database_path, "tool"),
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


def _encode_namespace_label(value: str) -> str:
    """Escape labels rejected by LangGraph while keeping identities reversible."""
    return value.replace("%", "%25").replace(".", "%2E")


def _decode_namespace_label(value: str) -> str:
    return value.replace("%2E", ".").replace("%25", "%")
