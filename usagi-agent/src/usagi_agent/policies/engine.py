"""Default PolicyEngine + Guardrail.

Policy is deterministic and versioned; the LLM cannot override it. The default
engine allows actions; the Tool module enforces ToolSpec.requires_approval. Guardrails are
structured pass/fail.
"""
from __future__ import annotations

from usagi_agent.ports import PolicyEngine, ToolCatalog, ToolContext
from usagi_agent.types.policy import GuardrailResult, PolicyDecision
from usagi_agent.types.refs import PrincipalRef


class DefaultPolicyEngine(PolicyEngine):
    def __init__(self, tool_catalog: ToolCatalog | None = None) -> None:
        self._tool_catalog = tool_catalog

    async def evaluate(
        self, *, principal: PrincipalRef, action: str, tool_name: str | None = None,
        arguments: dict | None = None, context: ToolContext,
    ) -> PolicyDecision:
        return PolicyDecision(effect="allow", reason_codes=["policy.default_allow"], obligations=[])


class DefaultGuardrail:
    """All four guardrails (input/pre-model/action/output) default to pass."""

    async def check(self, payload_ref, ctx: ToolContext) -> GuardrailResult:
        return GuardrailResult(passed=True, reason_codes=[])
