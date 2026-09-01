"""Top-level stable Port surface.

One file per capability domain (context/model/tool/memory/policy) — no grab-bag. This and
``usagi_agent.types`` / ``usagi_agent.api`` are the three stable cross-module surfaces;
modules must not import each other's internals, only these.
"""
from usagi_agent.ports.context import (
    DataAccessContext,
    GovernedExecutionContext,
    HealthStatus,
    StageContext,
    ToolContext,
)
from usagi_agent.ports.memory import (
    MemoryManager,
    MemoryMutationResult,
    MemoryRecallResult,
)
from usagi_agent.ports.model import ModelAdapter
from usagi_agent.ports.policy import Guardrail, PolicyEngine
from usagi_agent.ports.tool import ReconcileCapableToolAdapter, RetrieverAdapter
from usagi_agent.tools.adapter import ToolAdapter

__all__ = [
    "DataAccessContext",
    "GovernedExecutionContext",
    "HealthStatus",
    "StageContext",
    "ToolContext",
    "MemoryManager",
    "MemoryMutationResult",
    "MemoryRecallResult",
    "ModelAdapter",
    "Guardrail",
    "PolicyEngine",
    "ReconcileCapableToolAdapter",
    "RetrieverAdapter",
    "ToolAdapter",
]
