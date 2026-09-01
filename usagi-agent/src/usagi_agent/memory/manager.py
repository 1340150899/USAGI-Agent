"""Default MemoryManager (design §22.5, §22.8).

v1 uses one shared MemoryManager. Default impl: read current user/session namespace,
return empty hits (no populated store), and accept proposals as no-ops. Real hybrid search
+ promote/revoke state machines are wired behind the same Port (§22.8).
"""
from __future__ import annotations

from usagi_agent.ports import (
    HealthStatus,
    MemoryManager,
    MemoryMutationResult,
    MemoryRecallResult,
    ToolContext,
)
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.policy import MemoryCandidate


class DefaultMemoryManager(MemoryManager):
    async def recall(self, query: RecallQuery, ctx: ToolContext) -> MemoryRecallResult:
        return MemoryRecallResult(hits=[], reason_codes=[])

    async def propose(self, candidate: MemoryCandidate, ctx: ToolContext) -> MemoryMutationResult:
        return MemoryMutationResult(status="applied", reason_codes=[])

    async def revoke(self, memory_id: str, ctx: ToolContext) -> MemoryMutationResult:
        return MemoryMutationResult(status="applied", reason_codes=[])

    async def health(self) -> HealthStatus:
        return "healthy"
