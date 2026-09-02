"""Construct and populate AgentManager without owning model declarations."""
from __future__ import annotations

from usagi_agent.agents.manager import AgentManager


class AgentManagerInitializer:
    @staticmethod
    def init(settings) -> AgentManager:
        return AgentManager(model_execution_mode=settings.model_execution_mode)
