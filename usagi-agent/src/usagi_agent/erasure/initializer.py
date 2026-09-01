"""Erasure initializer (design §24.5, init-tree node).

Constructs the ErasureCoordinator from the injected Ports. Init only; the coordinator's
``request_erasure`` runs later. The independent ErasureWorkflow LangGraph is a TODO(§24.5).
"""
from __future__ import annotations

from usagi_agent.erasure.coordinator import ErasureCoordinator
from usagi_agent.observability import ObservabilityProvider
from usagi_agent.persistence.backend import InfrastructurePorts


class ErasureInitializer:
    @staticmethod
    def init(
        ports: InfrastructurePorts, observability: ObservabilityProvider,
        tenant_id: str = "default",
    ) -> ErasureCoordinator:
        return ErasureCoordinator(ports=ports, observability=observability, tenant_id=tenant_id)
