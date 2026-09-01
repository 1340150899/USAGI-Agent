"""Base class for executable tools."""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.types.tool import ToolSpec


class ToolAdapter(ABC):
    """A tool owns both its model-facing declaration and its implementation."""

    spec: ToolSpec

    @abstractmethod
    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> BaseModel | dict[str, object]:
        raise NotImplementedError

    async def health(self) -> HealthStatus:
        return "healthy"

    async def shutdown(self) -> None:
        return None
