"""Memory package with a lazy manager export to keep Port imports acyclic."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from usagi_agent.memory.manager import DefaultMemoryManager
    from usagi_agent.memory.store import MemoryStores

__all__ = ["DefaultMemoryManager", "MemoryStores"]


def __getattr__(name: str):
    if name == "DefaultMemoryManager":
        from usagi_agent.memory.manager import DefaultMemoryManager

        return DefaultMemoryManager
    if name == "MemoryStores":
        from usagi_agent.memory.store import MemoryStores

        return MemoryStores
    raise AttributeError(name)
