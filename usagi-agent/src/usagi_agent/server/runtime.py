"""Service-lifetime owner for all shared framework instances."""
from __future__ import annotations

import inspect

from pydantic import BaseModel, Field

from usagi_agent.kernel.runtime import KernelRuntime
from usagi_agent.ports import HealthStatus


class HealthReport(BaseModel):
    healthy: bool
    components: dict[str, HealthStatus] = Field(default_factory=dict)


class ServerRuntime:
    def __init__(
        self,
        *,
        settings,
        observability,
        persistence,
        agent_manager,
        tool_manager,
        memory_manager,
        policy_engine,
        guardrail,
        pipeline_compiler,
        scenario_registry,
        erasure_coordinator,
        kernel_components=None,
    ) -> None:
        self.settings = settings
        self.observability = observability
        self.persistence = persistence
        self.agent_manager = agent_manager
        self.tool_manager = tool_manager
        self.memory_manager = memory_manager
        self.policy_engine = policy_engine
        self.guardrail = guardrail
        self.pipeline_compiler = pipeline_compiler
        self.scenario_registry = scenario_registry
        self.erasure_coordinator = erasure_coordinator
        self.kernel_components = kernel_components
        self.kernel_runtime = KernelRuntime(self)
        self._closed = False

    def validate_ready(self) -> None:
        if self._closed:
            raise RuntimeError("ServerRuntime is shut down")
        if len(self.scenario_registry) == 0:
            raise RuntimeError("no ScenarioRuntime has been registered")

    async def health(self) -> HealthReport:
        components: dict[str, HealthStatus] = {}
        for name, component in (
            ("agent_manager", self.agent_manager),
            ("memory_manager", self.memory_manager),
            ("policy_engine", self.policy_engine),
            ("guardrail", self.guardrail),
            ("observability", self.observability),
        ):
            components[name] = await _health_status(component)
        tool_health: dict[str, HealthStatus]
        try:
            tool_health = await self.tool_manager.health()
        except Exception:
            tool_health = {"manager": "unhealthy"}
        components.update({f"tool:{name}": status for name, status in tool_health.items()})
        seen: set[int] = set()
        for name, component in vars(self.persistence).items():
            if id(component) in seen:
                continue
            seen.add(id(component))
            components[f"persistence:{name}"] = await _health_status(component)
        return HealthReport(
            healthy=all(value == "healthy" for value in components.values()),
            components=components,
        )

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        await _attempt_close(self.tool_manager, errors)
        await _attempt_close(self.agent_manager, errors)
        for component in (self.memory_manager, self.policy_engine, self.guardrail):
            await _attempt_close(component, errors)
        seen: set[int] = set()
        for component in vars(self.persistence).values():
            if id(component) not in seen:
                seen.add(id(component))
                await _attempt_close(component, errors)
        await _attempt_close(self.observability, errors)
        if errors:
            raise ExceptionGroup("one or more service resources failed to shut down", errors)


async def _close(component: object) -> None:
    close = getattr(component, "shutdown", None) or getattr(component, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _attempt_close(component: object, errors: list[Exception]) -> None:
    try:
        await _close(component)
    except Exception as exc:
        errors.append(exc)


async def _health_status(component: object) -> HealthStatus:
    health = getattr(component, "health", None)
    if health is None:
        return "healthy"
    try:
        result = health()
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        return "unhealthy"
    if isinstance(result, bool):
        return "healthy" if result else "unhealthy"
    if result in {"healthy", "degraded", "unhealthy"}:
        return result
    return "unhealthy"
