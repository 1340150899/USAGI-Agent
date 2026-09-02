from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from usagi_agent.api.errors import SafeError
from usagi_agent.pipelines.rules.stage import RuleExecutionError

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class StageProcessor:
    def __init__(self, runtime: "ServerRuntime", agent: "AgentSpec") -> None:
        self.runtime = runtime
        self.agent = agent

    @staticmethod
    def raise_on_error(error: RuleExecutionError, rule_name: str) -> NoReturn:
        message = f"rule {rule_name!r} failed"
        if error.message:
            message = f"{message}: {error.message}"
        raise SafeError(message, reason_code=error.reason_code)
