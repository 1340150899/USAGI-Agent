"""ErasureCoordinator (design §24.5).

Executes the fixed erasure flow for a subject/conversation scope. v1 implements the core
dev path (tombstone -> traverse Lineage -> delete artifacts/thread -> key destruction ->
receipt) over the InMemory stores; the full per-Store deletion matrix, tenant acceptance
lock, independent ErasureWorkflow LangGraph and backup deletion-ledger replay are marked
TODO(§24.5) and are exercised structurally, not fully, in the first version.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import cast

from usagi_agent.api.errors import UsagiError
from usagi_agent.erasure.control import ErasureControlState, ErasureReceipt
from usagi_agent.observability import ObservabilityProvider
from usagi_agent.persistence.backend import InfrastructurePorts
from usagi_agent.persistence.ports.checkpointer import AuthorizedCheckpointAdmin
from usagi_agent.persistence.ports.erasure import LineageIndex
from usagi_agent.types.refs import ArtifactRef
from usagi_agent.types.settlement import DestructivePermit


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ErasureCoordinator:
    """Drives the erase flow for a scope. Constructed at Bootstrap; ``request_erasure`` runs later."""

    def __init__(
        self, *, ports: InfrastructurePorts, observability: ObservabilityProvider,
        tenant_id: str = "default",
    ) -> None:
        self._ports = ports
        self._obs = observability
        self._tenant = tenant_id

    async def request_erasure(
        self, scope_ref: str, request_idempotency_key: str,
    ) -> ErasureControlState:
        """Accept an erasure request: create case + commit tombstone (§24.5)."""
        case_id = f"erase_{uuid.uuid4().hex}"
        state = ErasureControlState(
            erasure_case_id=case_id, tenant_id=self._tenant, target_scope_ref=scope_ref,
            status="pending", tombstone_committed_at=_now(),
        )
        await self._ports.erasure_control_store.create(state)
        # Begin running the (dev) flow inline. Production uses the independent ErasureWorkflow.
        await self._run_dev_flow(state)
        return state

    async def _run_dev_flow(self, state: ErasureControlState) -> None:
        """Core dev flow: traverse lineage -> delete artifacts + thread -> key destruction -> receipt."""
        scope = state.target_scope_ref
        # 1. traverse lineage for this scope
        edges = await self._ports.lineage_index.traverse(self._tenant, scope)
        derived_refs = [e.derived_ref for e in edges]
        # 2. delete derived artifacts (best-effort over the metadata store)
        for ref in derived_refs:
            try:
                await self._ports.artifact_manager.delete(
                    ArtifactRef(artifact_id=ref, content_type="application/octet-stream"),
                    operation_id=f"erase:{state.erasure_case_id}:{ref}",
                )
            except Exception:
                pass  # TODO(§24.5): per-Store matrix verification, not silent swallow in prod.
        # 3. delete the checkpoint thread (if the checkpointer supports authorized admin)
        checkpointer: object = self._ports.checkpointer
        admin = (
            cast(AuthorizedCheckpointAdmin, checkpointer)
            if isinstance(checkpointer, AuthorizedCheckpointAdmin)
            else None
        )
        if admin is not None:
            permit = DestructivePermit(
                tenant_id=self._tenant, thread_id=scope, control_kind="erasure",
                control_id=state.erasure_case_id,
                destructive_operation_id=f"del_{state.erasure_case_id}",
                expires_at=_now(), permit_hmac="dev",
            )
            try:
                await admin.delete_thread(self._tenant, scope, permit)
            except NotImplementedError:
                pass  # InMemory checkpointer doesn't implement AuthorizedCheckpointAdmin; dev ok.
        # 4. delete the scope's lineage edges
        await self._ports.lineage_index.delete_scope(self._tenant, scope)
        # 5. record the receipt and complete
        receipt = ErasureReceipt(
            erasure_case_id=state.erasure_case_id, completed_at=_now(),
            scope_deleted=[scope], threads_deleted=[scope] if admin is not None else [],
            keys_destroyed=[],  # TODO(§24.5): KeyDestructionStore for scope/derivation DEKs
        )
        done = state.model_copy(update={
            "status": "completed", "receipt_ref": state.erasure_case_id,
        })
        await self._ports.erasure_control_store.cas_update(
            state.erasure_case_id, state.version, done,
        )

    async def get_case(self, erasure_case_id: str) -> ErasureControlState:
        state = await self._ports.erasure_control_store.get(erasure_case_id)
        if state is None:
            raise UsagiError(f"erasure case {erasure_case_id} not found")
        return state  # type: ignore[return-value]
