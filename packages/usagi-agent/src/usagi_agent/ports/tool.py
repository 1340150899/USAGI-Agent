"""Tool Ports (design §21.3, §21.5, §16.4).

ToolAdapter / ToolRuntime / RetrieverAdapter Protocols + ResolvedTool. They reference the
concrete Tool data records in :mod:`usagi_agent.types.tool` (no forward placeholders).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from usagi_agent.ports.context import ToolContext
from usagi_agent.types.context import RecallCandidate, RecallQuery
from usagi_agent.types.tool import ReconcileResult, ToolReconcileRequest


@runtime_checkable
class ReconcileCapableToolAdapter(Protocol):
    async def reconcile(self, request: ToolReconcileRequest, ctx: ToolContext) -> ReconcileResult: ...


@runtime_checkable
class RetrieverAdapter(Protocol):
    """Read-only candidate fetcher for RecallSourcesRule (§16.4). Never executes writes."""

    async def retrieve(self, query: RecallQuery, ctx: ToolContext) -> list[RecallCandidate]: ...
