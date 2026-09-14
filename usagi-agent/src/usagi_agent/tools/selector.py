"""Tool selection.

V1 behavior is the agent allowlist verbatim. Future implementations
(semantic recall, token-budget pruning, ToolDiscovery) replace this class
without touching the ContextBuild call site.
"""
from __future__ import annotations

from collections.abc import Iterable

from usagi_agent.ports.tool import ToolCatalog
from usagi_agent.types.tool import ToolSpec


class AllowlistSelector:
    def __init__(self, tool_catalog: ToolCatalog) -> None:
        self._tool_catalog = tool_catalog

    async def select(
        self, allowed_tools: Iterable[str], token_budget: int
    ) -> tuple[ToolSpec, ...]:
        return self._tool_catalog.get_specs(tuple(allowed_tools))
