"""Construct service resources without loading any business scenario."""
from __future__ import annotations

from usagi_agent.agents import AgentManagerInitializer
from usagi_agent.erasure import ErasureInitializer
from usagi_agent.kernel import KernelInitializer
from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.observability import ObservabilityInitializer
from usagi_agent.persistence.backend import PersistenceInitializer
from usagi_agent.pipelines.compiler import PipelineCompiler
from usagi_agent.policies.engine import DefaultGuardrail, DefaultPolicyEngine
from usagi_agent.scenarios import ScenarioRuntimeRegistry
from usagi_agent.server.runtime import ServerRuntime
from usagi_agent.tools import ToolInitializer


class ServiceRuntimeInitializer:
    @staticmethod
    def init(settings) -> ServerRuntime:
        if settings.persistence_backend == "sqlite":
            settings.require_durable()
        observability = ObservabilityInitializer.init(settings)
        persistence = PersistenceInitializer.init(settings, observability)
        tool_manager = ToolInitializer.init(persistence)
        memory_manager = DefaultMemoryManager(path=settings.memory_path)
        policy_engine = DefaultPolicyEngine()
        guardrail = DefaultGuardrail()
        kernel_components = KernelInitializer.init(persistence, observability)
        erasure = ErasureInitializer.init(
            persistence, observability, tenant_id=settings.tenant_id
        )
        agent_manager = AgentManagerInitializer.init(settings)
        
        return ServerRuntime(
            settings=settings,
            observability=observability,
            persistence=persistence,
            agent_manager=agent_manager,
            tool_manager=tool_manager,
            memory_manager=memory_manager,
            policy_engine=policy_engine,
            guardrail=guardrail,
            pipeline_compiler=PipelineCompiler(),
            scenario_registry=ScenarioRuntimeRegistry(),
            erasure_coordinator=erasure,
            kernel_components=kernel_components,
        )
