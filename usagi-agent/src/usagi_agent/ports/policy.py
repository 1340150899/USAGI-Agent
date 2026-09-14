"""Policy / Guardrail Ports."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from usagi_agent.ports.context import ToolContext
from usagi_agent.types.policy import GuardrailResult, PolicyDecision
from usagi_agent.types.refs import ArtifactRef, PrincipalRef


@runtime_checkable
class PolicyEngine(Protocol):
    """Deterministic, versioned; LLM cannot override."""

    async def evaluate(
        self, *, principal: PrincipalRef, action: str, tool_name: str | None = None,
        arguments: dict | None = None, context: ToolContext,
    ) -> PolicyDecision: ...


@runtime_checkable
class Guardrail(Protocol):
    async def check(self, payload_ref: ArtifactRef, ctx: ToolContext) -> GuardrailResult: ...
