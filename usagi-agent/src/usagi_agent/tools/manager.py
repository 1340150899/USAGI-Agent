"""Service-lifetime tool ownership and execution."""
from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from usagi_agent.api.errors import DuplicateToolError, UnknownToolError
from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.action import ToolObservation
from usagi_agent.types.tool import ToolSpec


class ToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, ToolAdapter] = {}

    def register(self, adapter: ToolAdapter) -> None:
        name = adapter.spec.name
        if name in self._tools:
            raise DuplicateToolError(name)
        self._tools[name] = adapter

    def register_many(self, adapters: Iterable[ToolAdapter]) -> None:
        for adapter in adapters:
            self.register(adapter)

    def resolve(self, name: str) -> ToolAdapter:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(name) from exc

    def get_spec(self, name: str) -> ToolSpec:
        return self.resolve(name).spec

    def all_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(adapter.spec for adapter in self._tools.values())

    def get_specs(self, names: Iterable[str]) -> tuple[ToolSpec, ...]:
        return tuple(self.get_spec(name) for name in names)

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        tool_call_id: str = "",
    ) -> ToolObservation:
        adapter = self.resolve(name)
        try:
            result = await adapter.execute(arguments, context)
            output = result.model_dump() if isinstance(result, BaseModel) else dict(result)
            return ToolObservation(
                tool_name=name,
                tool_call_id=tool_call_id,
                status="success",
                output=output,
            )
        except Exception as exc:
            return ToolObservation(
                tool_name=name,
                tool_call_id=tool_call_id,
                status="failed",
                error_code="tool.execution_failed",
                error_message=type(exc).__name__,
            )

    async def health(self) -> dict[str, HealthStatus]:
        statuses: dict[str, HealthStatus] = {}
        for name, adapter in self._tools.items():
            try:
                statuses[name] = await adapter.health()
            except Exception:
                statuses[name] = "unhealthy"
        return statuses

    async def shutdown(self) -> None:
        errors: list[Exception] = []
        for adapter in reversed(tuple(self._tools.values())):
            try:
                await adapter.shutdown()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("one or more tools failed to shut down", errors)
