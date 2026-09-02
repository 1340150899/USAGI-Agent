"""Memory package with a lazy manager export to keep Port imports acyclic."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from usagi_agent.memory.manager import DefaultMemoryManager

__all__ = ["DefaultMemoryManager"]


def __getattr__(name: str):
    if name == "DefaultMemoryManager":
        from usagi_agent.memory.manager import DefaultMemoryManager

        return DefaultMemoryManager
    raise AttributeError(name)
