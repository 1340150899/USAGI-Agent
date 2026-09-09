"""Construct service resources without loading any business scenario."""
from __future__ import annotations

from usagi_agent.agents import AgentManagerInitializer
from usagi_agent.erasure import ErasureInitializer
from usagi_agent.kernel import KernelInitializer
from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.memory.store import MemoryStores
from usagi_agent.observability import ObservabilityInitializer
from usagi_agent.persistence.backend import PersistenceInitializer
from usagi_agent.pipelines.compiler import PipelineCompiler
from usagi_agent.policies.engine import DefaultGuardrail, DefaultPolicyEngine
from usagi_agent.scenarios import ScenarioRuntimeRegistry
from usagi_agent.sessions import SessionManager
from usagi_agent.server.runtime import ServerRuntime
from usagi_agent.tools import ToolInitializer


class ServiceRuntimeInitializer:
    @staticmethod
    def init(settings, *, tool_specs=None) -> ServerRuntime:
        settings.require_durable()
        observability = ObservabilityInitializer.init(settings)
        persistence = PersistenceInitializer.init(settings, observability)
        tool_manager = ToolInitializer.init(persistence, specs=tool_specs)
        memory_manager = DefaultMemoryManager(
            stores=MemoryStores.sqlite(settings.selected_sqlite_path())
        )
        policy_engine = DefaultPolicyEngine(tool_manager)
        guardrail = DefaultGuardrail()
        kernel_components = KernelInitializer.init(persistence, observability)
        erasure = ErasureInitializer.init(
            persistence, observability, tenant_id=settings.tenant_id
        )
        agent_manager = AgentManagerInitializer.init(settings)
        session_manager = SessionManager(persistence.session_store)
        
        return ServerRuntime(
            settings=settings,
            observability=observability,
            persistence=persistence,
            agent_manager=agent_manager,
            session_manager=session_manager,
            tool_manager=tool_manager,
            memory_manager=memory_manager,
            policy_engine=policy_engine,
            guardrail=guardrail,
            pipeline_compiler=PipelineCompiler(),
            scenario_registry=ScenarioRuntimeRegistry(),
            erasure_coordinator=erasure,
            kernel_components=kernel_components,
        )
