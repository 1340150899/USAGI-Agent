"""Checkpointer Port (design §10.6).

``FencedCheckpointer`` is a *custom* LangGraph ``BaseCheckpointSaver`` implementation,
not a decorator that wraps a standard saver. Concrete impls (InMemory / SQLite) live in
``persistence.inmemory`` / ``persistence.sqlite`` and subclass ``BaseCheckpointSaver``
directly. ``put/aput`` and ``put_writes/aput_writes`` atomically verify the fencing gate
(tenant/run/fencing_token/lease_owner/DB-clock expiry/status) inside the same DB
transaction as the checkpoint write.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver

from usagi_agent.types.settlement import DestructivePermit

# Concrete FencedCheckpointer impls subclass BaseCheckpointSaver; re-exported here so
# the Port surface names the contract.
__all__ = ["BaseCheckpointSaver", "AuthorizedCheckpointAdmin"]


@runtime_checkable
class AuthorizedCheckpointAdmin(Protocol):
    """Business-facing thread deletion (§10.6). ``run_id``/tenant come from binding."""

    async def delete_thread(
        self, tenant_id: str, thread_id: str, permit: DestructivePermit
    ) -> None: ...
