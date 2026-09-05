"""Agent: AgentSpec, Manager, Coordinator (design §13, §14)."""
from usagi_agent.agents.manager import AgentManager
from usagi_agent.agents.initializer import AgentManagerInitializer
from usagi_agent.agents.spec import AgentSpec
from usagi_agent.agents.model_adapter_factory import create_model_adapter

__all__ = [
    "AgentManager",
    "AgentManagerInitializer",
    "AgentSpec",
    "create_model_adapter",
]
