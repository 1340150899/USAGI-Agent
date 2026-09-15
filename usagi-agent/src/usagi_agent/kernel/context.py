"""Immutable context belonging to one run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.agents.schemas import OutputContract
from usagi_agent.types.refs import PrincipalRef


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Authenticated caller identity supplied at the public Server boundary."""

    principal: PrincipalRef
    authorization_scope: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    thread_id: str
    tenant_id: str
    principal: PrincipalRef
    authorization_scope: tuple[str, ...]
    deadline: datetime | None
    fencing_token: int
    trace_parent: str | None = None
    session_id: str | None = None
    output_contract: OutputContract | None = None

    @property
    def memory_session_id(self) -> str:
        return self.session_id or self.thread_id

    def to_tool_context(self) -> ToolContext:
        return ToolContext(
            execution=GovernedExecutionContext(
                tenant_id=self.tenant_id,
                principal=self.principal,
                authorization_scope=self.authorization_scope,
                absolute_deadline=self.deadline.isoformat() if self.deadline else None,
                control_kind="run",
                control_id=self.run_id,
                fencing_token=self.fencing_token,
                session_id=self.memory_session_id,
            )
        )

    @classmethod
    def from_graph_config(cls, config: Mapping[str, Any]) -> "RunContext":
        configurable = config.get("configurable", {})
        context = configurable.get("run_context")
        if not isinstance(context, cls):
            raise TypeError("graph config is missing an immutable RunContext")
        return context
