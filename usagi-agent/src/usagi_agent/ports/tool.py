"""Tool Ports (design §21.3, §21.5, §16.4).

ToolRuntime / ToolSource / ToolExecutionBackend / ToolSelector Protocols +
RetrieverAdapter. v1 implementations: ToolRuntime is satisfied by
``tools.manager.ToolManager``; the remaining Protocols are extension seams
(HTTP/MCP adapters, execution sandboxes, dynamic tool search) — declared here
so later implementations do not move call sites.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from usagi_agent.ports.context import ToolContext
from usagi_agent.types.action import ToolObservation
from usagi_agent.types.context import RecallCandidate, RecallQuery
from usagi_agent.types.tool import ReconcileResult, ToolReconcileRequest, ToolSpec


@runtime_checkable
class ExecutableTool(Protocol):
    """The structural contract every adapter kind (python/http/mcp) satisfies."""

    spec: ToolSpec

    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> BaseModel | dict[str, object]: ...


@runtime_checkable
class ToolRuntime(Protocol):
    """Atomic execution service (§21.5); owns no graph control flow."""

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        tool_call_id: str = "",
        operation_id: str | None = None,
    ) -> ToolObservation: ...


@runtime_checkable
class ToolCatalog(Protocol):
    """Read-only registry view used by selection and bootstrap validation."""

    def get_specs(self, names: Iterable[str]) -> tuple[ToolSpec, ...]: ...


@runtime_checkable
class ToolSource(Protocol):
    """Bootstrap-time provider of tools; HTTP and MCP are future sources."""

    async def load(self) -> tuple[Any, ...]: ...


@runtime_checkable
class ToolExecutionBackend(Protocol):
    """Execution environment seam (in-process today, sandbox later)."""

    async def run(
        self,
        tool: ExecutableTool,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> BaseModel | dict[str, object]: ...


@runtime_checkable
class ToolSelector(Protocol):
    """Choose the tool declarations a model sees (§21.4)."""

    async def select(
        self, allowed_tools: Iterable[str], token_budget: int
    ) -> tuple[ToolSpec, ...]: ...


@runtime_checkable
class ReconcileCapableToolAdapter(Protocol):
    async def reconcile(self, request: ToolReconcileRequest, ctx: ToolContext) -> ReconcileResult: ...


@runtime_checkable
class RetrieverAdapter(Protocol):
    """Read-only candidate fetcher for RecallSourcesRule (§16.4). Never executes writes."""

    async def retrieve(self, query: RecallQuery, ctx: ToolContext) -> list[RecallCandidate]: ...
