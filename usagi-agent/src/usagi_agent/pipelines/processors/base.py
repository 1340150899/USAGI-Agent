from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, NoReturn

from usagi_agent.api.errors import SafeError
from usagi_agent.observability import operation
from usagi_agent.pipelines.rules.stage import RuleExecutionError

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class StageProcessor:
    def __init__(self, runtime: ServerRuntime, agent: AgentSpec) -> None:
        self.runtime = runtime
        self.agent = agent

    async def run_rule(self, stage: str, rule_name: str, call: Awaitable):
        """Run one configured rule under a stable rule span and metrics."""
        provider = getattr(self.runtime, "observability", None)
        if provider is None:
            return await call
        with operation(
            provider,
            "rule.execute",
            **{
                "usagi.pipeline.stage.name": stage,
                "usagi.rule.name": rule_name,
                "usagi.agent.name": getattr(self.agent, "id", None),
            },
        ) as telemetry:
            result = await call
            if isinstance(result, RuleExecutionError):
                telemetry.set_outcome("failed")
                telemetry.set_attribute("error.type", result.reason_code)
            return result

    @staticmethod
    def raise_on_error(error: RuleExecutionError, rule_name: str) -> NoReturn:
        message = f"rule {rule_name!r} failed"
        if error.message:
            message = f"{message}: {error.message}"
        raise SafeError(message, reason_code=error.reason_code)
