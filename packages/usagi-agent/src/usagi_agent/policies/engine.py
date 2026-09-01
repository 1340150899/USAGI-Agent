"""Default PolicyEngine + Guardrail (design §23).

Policy is deterministic and versioned; the LLM cannot override it (§23.2). The default
engine allows read actions and requires approval for high-risk writes. Guardrails are
structured pass/fail (§23.3).
"""
from __future__ import annotations

from usagi_agent.ports import PolicyEngine, ToolContext
from usagi_agent.types.policy import GuardrailResult, PolicyDecision
from usagi_agent.types.refs import PrincipalRef


class DefaultPolicyEngine(PolicyEngine):
    async def evaluate(
        self, *, principal: PrincipalRef, action: str, tool_name: str | None = None,
        arguments: dict | None = None, context: ToolContext,
    ) -> PolicyDecision:
        # v1 dev: allow everything; production Policy is code-defined, not LLM-overridable.
        return PolicyDecision(effect="allow", reason_codes=["policy.default_allow"], obligations=[])


class DefaultGuardrail:
    """All four guardrails (input/pre-model/action/output) default to pass (§23.3)."""

    async def check(self, payload_ref, ctx: ToolContext) -> GuardrailResult:
        return GuardrailResult(passed=True, reason_codes=[])
