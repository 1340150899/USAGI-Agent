"""Default PolicyEngine + Guardrail (design §23).

Policy is deterministic and versioned; the LLM cannot override it (§23.2). The default
engine allows read actions and requires approval for high-risk writes. Guardrails are
structured pass/fail (§23.3).
"""
from __future__ import annotations

from usagi_agent.api.errors import UnknownToolError
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
        if action == "tool.execute" and tool_name and self._tool_catalog is not None:
            try:
                risk = self._tool_catalog.get_spec(tool_name).risk
            except UnknownToolError:
                risk = None
            if risk == "high_risk_write":
                return PolicyDecision(
                    effect="require_approval",
                    reason_codes=["policy.high_risk_write"],
                    obligations=[],
                )
        return PolicyDecision(effect="allow", reason_codes=["policy.default_allow"], obligations=[])


class DefaultGuardrail:
    """All four guardrails (input/pre-model/action/output) default to pass (§23.3)."""

    async def check(self, payload_ref, ctx: ToolContext) -> GuardrailResult:
        return GuardrailResult(passed=True, reason_codes=[])
