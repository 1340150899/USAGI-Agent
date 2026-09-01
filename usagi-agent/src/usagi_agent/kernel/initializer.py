"""Construct Kernel cross-cutting managers from service-owned persistence ports.

This initializer performs no run-time execution and has no dependency on business
ScenarioConfig or compiled graphs.
"""
from __future__ import annotations

from usagi_agent.kernel.managers import (
    BudgetManager,
    CancellationManager,
    ErrorMapper,
    EventPublisher,
    KernelComponents,
    LifecycleManager,
    MiddlewareChain,
)
from usagi_agent.observability import ObservabilityProvider
from usagi_agent.persistence.backend import InfrastructurePorts


class KernelInitializer:
    @staticmethod
    def init(ports: InfrastructurePorts, observability: ObservabilityProvider) -> KernelComponents:
        budget = BudgetManager(ports)
        cancellation = CancellationManager(ports)
        middleware = MiddlewareChain(budget, cancellation)
        return KernelComponents(
            budget=budget,
            cancellation=cancellation,
            middleware=middleware,
            errors=ErrorMapper(),
            events=EventPublisher(ports),
            lifecycle=LifecycleManager(),
        )
