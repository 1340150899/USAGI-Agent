"""Model Port (design §18)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.types.model import ModelRequest, ModelResponse
from usagi_agent.types.refs import AdapterRef


@runtime_checkable
class ModelAdapter(Protocol):
    """Internal AgentManager model-execution strategy contract."""

    adapter_ref: AdapterRef

    async def generate(self, request: ModelRequest, ctx: ToolContext) -> ModelResponse: ...

    async def health(self) -> HealthStatus: ...
