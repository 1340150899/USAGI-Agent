"""Kernel: Runtime, lifecycle, budget, fencing, settlement (design §10).

The Kernel holds the *execution* root (KernelRuntime) and the run-control / lease state
machines. Construction happens in :mod:`usagi_agent.kernel.initializer` (init only); the
Runtime is the only piece that runs at Run time.
"""
from usagi_agent.kernel.initializer import KernelInitializer
from usagi_agent.kernel.context import AuthContext, RunContext
from usagi_agent.kernel.managers import KernelComponents
from usagi_agent.kernel.runtime import (
    KernelRuntime,
    LeaseManager,
    RunControlManager,
)

__all__ = [
    "KernelInitializer",
    "AuthContext",
    "KernelComponents",
    "KernelRuntime",
    "LeaseManager",
    "RunContext",
    "RunControlManager",
]
