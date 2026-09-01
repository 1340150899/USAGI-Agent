"""Agent registry and shared model gateway."""
from __future__ import annotations

from usagi_agent.agents.spec import AgentSpec
from usagi_agent.api.errors import DuplicateAgentError, UnknownAgentError
from usagi_agent.kernel.context import RunContext
from usagi_agent.ports import HealthStatus, ModelAdapter
from usagi_agent.types.model import ModelRequest, ModelResponse


class AgentManager:
    def __init__(self, default_model_adapter: ModelAdapter) -> None:
        self._default_model_adapter = default_model_adapter
        self._agents: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        if spec.id in self._agents:
            raise DuplicateAgentError(spec.id)
        self._agents[spec.id] = spec

    def get(self, agent_id: str) -> AgentSpec:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(agent_id) from exc

    def get_prompt(self, agent_id: str) -> tuple[str, str]:
        """Return the immutable prompt reference and template owned by an Agent."""
        agent = self.get(agent_id)
        return agent.prompt, agent.prompt_template

    def render_prompt(
        self, agent_id: str, variables: dict[str, object] | None = None
    ) -> str:
        """Render an Agent prompt; missing template variables fail fast."""
        _, template = self.get_prompt(agent_id)
        if not variables:
            return template
        return template.format_map(variables)

    async def generate(
        self, *, agent_id: str, request: ModelRequest, context: RunContext
    ) -> ModelResponse:
        self.get(agent_id)
        return await self._default_model_adapter.generate(request, context.to_tool_context())

    async def health(self) -> HealthStatus:
        return await self._default_model_adapter.health()

    async def shutdown(self) -> None:
        close = getattr(self._default_model_adapter, "shutdown", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
