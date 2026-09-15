"""Kernel Runtime — execution root.

This module is *execution only*. Construction of its dependencies happens in
:mod:`usagi_agent.kernel.initializer` (init). The Runtime:

* resolves an immutable ScenarioRuntime by ``scenario_key`` (never recompiles at Run time)
* performs API ingress (idempotency namespace, client fingerprint, durable dedup, atomic
  RunStartRequest + ExecutionContext + RunControl + start-outbox)
* drives the compiled LangGraph graph (dev: inline worker; production separates the Worker)
* projects RunControlState -> RunOutcome, never exposing tokens via GET.

Fencing / lease / budget CAS all go through the injected Stores; the Runtime holds no
mutable run state itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.types import Command
from pydantic import SecretStr

from usagi_agent.api.errors import (
    IdempotencyConflictError,
    LeaseLost,
    PolicyDeniedError,
    UsagiError,
)
from usagi_agent.kernel.context import AuthContext, RunContext
from usagi_agent.observability import ObservabilityProvider, span
from usagi_agent.persistence.backend import InfrastructurePorts
from usagi_agent.persistence.ports.outbox import DurableOutboxEvent
from usagi_agent.persistence.ports.run_lifecycle import (
    ExecutionContextSnapshot,
    RunControlState,
    RunStartRequestRecord,
)
from usagi_agent.pipelines.artifacts import put_model
from usagi_agent.agents.schemas import OutputContract
from usagi_agent.sessions.types import SessionMessage
from usagi_agent.types.budget import BudgetUsage
from usagi_agent.types.refs import (
    ArtifactOwner,
    ArtifactRef,
    PrincipalRef,
)
from usagi_agent.types.run import (
    ApprovalResume,
    CancellationReasonCode,
    Completed,
    Failed,
    InterruptDescriptor,
    ResumeTokenEnvelope,
    RunHandle,
    Running,
    RunOutcome,
    RunStartRequest,
    Suspended,
)
from usagi_agent.types.settlement import FencingGate

_LEASE_TTL_SECONDS = 60
log = logging.getLogger(__name__)
_DEV_HMAC_KEY = b"usagi-dev-fingerprint-key"  # TODO: tenant-scoped HMAC key service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical(obj: Any) -> str:
    """Stable canonical JSON (rejects NaN/Infinity; sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _client_fingerprint(
    tenant_id: str,
    namespace: str,
    session_id: str,
    request: RunStartRequest,
) -> str:
    payload = _canonical(
        {
            "tenant": tenant_id,
            "namespace": namespace,
            "scenario": request.scenario_key,
            "session_id": session_id,
            "input": request.input.model_dump(mode="json"),
            "content_parts": [
                part.model_dump(mode="json") for part in request.content_parts
            ],
            "deadline": request.options.absolute_deadline.isoformat()
            if request.options.absolute_deadline
            else None,
            "delegation_ref": request.options.delegation_ref,
            "context_compaction": request.options.context_compaction,
        }
    )
    return hmac.new(_DEV_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()


class LeaseManager:
    """Lease acquire/renew/release on the independent ``lease_version`` axis."""

    def __init__(self, ports: InfrastructurePorts) -> None:
        self._ports = ports

    async def acquire(self, run_id: str, owner: str, ttl: int = _LEASE_TTL_SECONDS) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        return await self._ports.run_control_store.cas_lease(
            run_id,
            expected_lease_version=state.lease_version,
            lease_owner=owner,
            lease_expires_at=_now() + timedelta(seconds=ttl),
            fencing_token=state.fencing_token + 1,
        )

    async def renew(self, run_id: str, owner: str, fencing_token: int, ttl: int = _LEASE_TTL_SECONDS) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        if state.lease_owner != owner or state.fencing_token != fencing_token:
            raise LeaseLost(f"lease lost for {run_id}")
        return await self._ports.run_control_store.cas_lease(
            run_id,
            expected_lease_version=state.lease_version,
            lease_owner=owner,
            lease_expires_at=_now() + timedelta(seconds=ttl),
            fencing_token=fencing_token,
        )


class RunControlManager:
    """Status CAS transitions on the ordinary ``version`` axis."""

    def __init__(self, ports: InfrastructurePorts) -> None:
        self._ports = ports

    async def to_suspended(self, run_id: str, checkpoint_id: str, digest: str) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        new = state.model_copy(update={
            "run_status": "suspended",
            "suspended_checkpoint_id": checkpoint_id,
            "interrupt_set_digest": digest,
        })
        return await self._ports.run_control_store.cas_update(run_id, state.version, new)

    async def to_completed(self, run_id: str, result_ref: ArtifactRef) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        new = state.model_copy(update={"run_status": "completed", "final_result_ref": result_ref})
        return await self._ports.run_control_store.cas_update(run_id, state.version, new)

    async def to_resume_accepted(self, run_id: str) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None or state.run_status != "suspended":
            raise UsagiError(f"run {run_id} is not suspended")
        resumed = state.model_copy(update={"run_status": "resume_accepted"})
        resumed = await self._ports.run_control_store.cas_update(run_id, state.version, resumed)
        return await self._ports.run_control_store.cas_lease(
            run_id,
            expected_lease_version=resumed.lease_version,
            lease_owner="usagi-worker",
            lease_expires_at=_now() + timedelta(seconds=_LEASE_TTL_SECONDS),
            fencing_token=resumed.fencing_token,
        )

    async def to_failed(self, run_id: str, reason_codes: list[str]) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        new = state.model_copy(update={"run_status": "failed", "reason_codes": reason_codes})
        return await self._ports.run_control_store.cas_update(run_id, state.version, new)

    async def request_cancel(self, run_id: str, reason: CancellationReasonCode) -> RunControlState:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found")
        if state.run_status in ("completed", "failed", "cancelled"):
            return state  # already terminal
        new = state.model_copy(update={
            "run_status": "cancel_requested",
            "cancel_requested_at": _now(),
            "cancellation_reason_code": reason,
        })
        bumped = await self._ports.run_control_store.cas_update(run_id, state.version, new)
        # Cancel also bumps fencing token (new owner/none) so stale workers stop writing.
        await self._ports.run_control_store.cas_lease(
            run_id,
            expected_lease_version=bumped.lease_version,
            lease_owner=None,
            lease_expires_at=None,
            fencing_token=bumped.fencing_token + 1,
        )
        final = await self._ports.run_control_store.get(run_id)
        # For v1 dev with no in-flight external ops, complete the cancellation immediately.
        if final is not None and final.run_status == "cancel_requested":
            cancelled = final.model_copy(update={
                "run_status": "cancelled",
                "cancelled_at": _now(),
            })
            return await self._ports.run_control_store.cas_update(run_id, final.version, cancelled)
        return final  # type: ignore[return-value]


class _KernelRuntime:
    """Private execution engine resolving precompiled scenarios."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._ports: InfrastructurePorts = runtime.persistence
        self._obs: ObservabilityProvider = runtime.observability
        self._tenant = runtime.settings.tenant_id
        self._principal = PrincipalRef(
            principal_kind="system", principal_opaque_id="usagi-runtime"
        )
        self._lease = LeaseManager(self._ports)
        self._rcm = RunControlManager(self._ports)
        self._components = runtime.kernel_components

    # --- public lifecycle API ---

    async def create_session(self, request: RunStartRequest, *, auth=None) -> SessionMessage:
        auth_context = self._resolve_auth(auth)
        uid = auth_context.principal.principal_opaque_id
        session_id = self._new_session_id(auth_context, request.request_idempotency_key)
        session = await self._runtime.session_manager.create(uid, session_id)
        handle = await self._start(
            request, auth=auth_context, session_id=session.id
        )
        return await self._session_message(session.id, handle)

    async def continue_session(
        self, session_id: str, request: RunStartRequest, *, auth=None
    ) -> SessionMessage:
        auth_context = self._resolve_auth(auth)
        session = await self._runtime.session_manager.get(session_id)
        if session is None:
            raise UsagiError(f"session {session_id} not found")
        if session.uid != auth_context.principal.principal_opaque_id:
            raise PolicyDeniedError("caller does not own this session")
        if session.status == "closed":
            raise UsagiError(f"session {session_id} is closed")

        pending = await self._ports.approval_store.list_pending_by_session(session_id)
        if session.status == "pending_approval" or pending:
            if len(pending) != 1:
                raise UsagiError(f"session {session_id} does not have one pending approval")
            task = pending[0]
            state = await self._ports.run_control_store.get(task.run_id)
            # Repair the exact inconsistent state produced by the former resume
            # bug: it persisted ``completed`` with an empty result while the
            # graph and approval store still had a live interrupt. This keeps
            # already-persisted conversations usable after upgrading.
            if (
                state is not None
                and state.run_status == "completed"
                and (
                    state.final_result_ref is None
                    or not state.final_result_ref.artifact_id
                )
                and state.suspended_checkpoint_id
            ):
                await self._rcm.to_suspended(
                    task.run_id,
                    state.suspended_checkpoint_id,
                    task.interrupt_id,
                )
            answer = str(getattr(request.input, "query", "")).strip().lower()
            handle = await self._build_handle(task.run_id)
            if answer not in {"yes", "no"}:
                return await self._session_message(session_id, handle, approval=task)
            current = handle.outcome
            if not isinstance(current, Suspended):
                raise UsagiError(f"run {task.run_id} is not suspended")
            descriptor = next(
                (item for item in current.interrupts if item.interrupt_id == task.interrupt_id),
                None,
            )
            if descriptor is None:
                raise UsagiError("pending approval interrupt is unavailable")
            token = await self.issue_resume_token(
                task.run_id, task.interrupt_id, descriptor.checkpoint_id, auth=auth_context
            )
            resumed = await self.resume(
                task.run_id,
                ApprovalResume(
                    interrupt_id=task.interrupt_id,
                    expected_checkpoint_id=descriptor.checkpoint_id,
                    resume_token=token.resume_token,
                    approval_id=task.approval_id,
                    expected_approval_version=task.version,
                    approval_scope=task.approval_scope,
                    action_hash=task.action_hash,
                    decision="approve" if answer == "yes" else "reject",
                ),
                auth=auth_context,
            )
            return await self._session_message(session_id, resumed)

        handle = await self._start(
            request, auth=auth_context, session_id=session_id
        )
        return await self._session_message(session_id, handle)

    async def get_run(self, run_id: str, *, auth=None) -> RunOutcome:
        await self._authorize_run(run_id, auth, "run.read")
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run {run_id} not found")
        return self._project(run_id, state)

    async def cancel(self, run_id: str, reason_code: CancellationReasonCode, *, auth=None) -> RunHandle:
        await self._authorize_run(run_id, auth, "run.cancel")
        with span(
            self._obs,
            "workflow.run",
            **{"usagi.workflow.operation": "cancel"},
        ) as telemetry:
            state = await self._rcm.request_cancel(run_id, reason_code)
            telemetry.set_outcome(state.run_status)
        return RunHandle(
            run_id=run_id, thread_id=run_id, scenario_key="",
            outcome=self._project(run_id, state), created_at=_now(),
        )

    async def resume(self, run_id: str, resume, *, auth=None) -> RunHandle:
        await self._authorize_run(run_id, auth, "run.resume")
        if not isinstance(resume, ApprovalResume):
            raise NotImplementedError("only approval resumes are supported by Agent pipelines")
        state = await self._ports.run_control_store.get(run_id)
        if state is None or state.run_status != "suspended":
            raise UsagiError(f"run {run_id} is not suspended")
        checkpoint_id = state.suspended_checkpoint_id or ""
        interrupt_id = state.interrupt_set_digest or ""
        if resume.expected_checkpoint_id != checkpoint_id or resume.interrupt_id != interrupt_id:
            raise PolicyDeniedError("resume checkpoint or interrupt does not match")
        expected_token = self._make_resume_token(run_id, interrupt_id, checkpoint_id)
        if not hmac.compare_digest(resume.resume_token.get_secret_value(), expected_token):
            raise PolicyDeniedError("invalid resume token")
        approval = await self._ports.approval_store.get(resume.approval_id)
        if (
            approval is None
            or approval.status != "pending"
            or approval.version != resume.expected_approval_version
            or approval.action_hash != resume.action_hash
            or approval.approval_scope != resume.approval_scope
        ):
            raise PolicyDeniedError("approval resume does not match the pending task")
        state = await self._rcm.to_resume_accepted(run_id)
        snapshot = await self._ports.execution_context_store.get(run_id)
        if snapshot is None:
            raise UsagiError(f"execution context {run_id} not found")
        scenario = self._runtime.scenario_registry.get(snapshot.scenario_key)
        session_id = approval.session_id
        if session_id is None:
            raise UsagiError("approval is not associated with a session")
        output_contract = self._runtime.agent_manager.output_contract(
            scenario.config.agent_id
        )
        gate = FencingGate(
            tenant_id=self._tenant,
            control_kind="run",
            control_id=run_id,
            lease_owner=state.lease_owner or "",
            fencing_token=state.fencing_token,
        )
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": snapshot.thread_id,
                "tenant_id": self._tenant,
                "checkpoint_id": checkpoint_id,
                "checkpoint_ns": "",
                "fencing_gate": gate,
                "run_context": RunContext(
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    tenant_id=snapshot.tenant_id,
                    principal=snapshot.original_principal,
                    authorization_scope=snapshot.authorization_scope,
                    deadline=snapshot.absolute_deadline,
                    fencing_token=state.fencing_token,
                    session_id=session_id,
                    output_contract=output_contract,
                ),
            }
        }
        with span(
            self._obs,
            "workflow.run",
            **{
                "usagi.workflow.operation": "resume",
                "usagi.scenario.name": snapshot.scenario_key,
            },
        ) as telemetry:
            heartbeat = asyncio.create_task(
                self._keep_lease_alive(run_id, state.lease_owner or "", state.fencing_token)
            )
            try:
                result = await scenario.compiled_graph.ainvoke(
                    Command(resume=resume.model_dump(mode="python")), config
                )
                await self._finish_graph_result(run_id, scenario, config, result, snapshot)
            except Exception as exc:
                log.exception("run %s failed while resuming the pipeline", run_id)
                telemetry.set_outcome("failed")
                telemetry.set_attribute("error.type", type(exc).__name__)
                await self._rcm.to_failed(run_id, [type(exc).__name__])
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            final_state = await self._ports.run_control_store.get(run_id)
            if final_state is not None:
                telemetry.set_outcome(final_state.run_status)

        return await self._build_handle(run_id)

    async def issue_resume_token(self, run_id: str, interrupt_id: str, expected_checkpoint_id: str, *, auth=None):
        await self._authorize_run(run_id, auth, "run.resume")
        state = await self._ports.run_control_store.get(run_id)
        if (
            state is None
            or state.run_status != "suspended"
            or state.interrupt_set_digest != interrupt_id
            or state.suspended_checkpoint_id != expected_checkpoint_id
        ):
            raise PolicyDeniedError("resume token request does not match suspended run")
        return ResumeTokenEnvelope(
            run_id=run_id,
            interrupt_id=interrupt_id,
            checkpoint_id=expected_checkpoint_id,
            credential_version=1,
            resume_token=SecretStr(
                self._make_resume_token(run_id, interrupt_id, expected_checkpoint_id)
            ),
        )

    # --- ingress + drive ---

    async def _start(
        self, request: RunStartRequest, *, auth=None, session_id: str,
    ) -> RunHandle:
        auth_context = self._resolve_auth(auth)
        namespace = f"{self._tenant}:{auth_context.principal.principal_opaque_id}"
        client_fp = _client_fingerprint(self._tenant, namespace, session_id, request)

        # Dedup BEFORE resolving the Bundle: same key + same fingerprint -> replay.
        existing = await self._ports.run_start_request_store.get_by_key(
            self._tenant, namespace, request.request_idempotency_key
        )
        if existing is not None:
            if existing.client_request_fingerprint != client_fp:
                raise IdempotencyConflictError(
                    f"idempotency conflict for key {request.request_idempotency_key!r}",
                    reason_code="run.idempotency_conflict",
                )
            return await self._await_existing_handle(existing.run_id)

        # First request: resolve immutable Bundle by scenario_key.
        scenario = self._runtime.scenario_registry.get(request.scenario_key)
        output_contract = self._runtime.agent_manager.output_contract(
            scenario.config.agent_id
        )
        bundle_fp = f"{self._runtime.settings.service_version}:{scenario.checksum}"

        run_id = _new_id("run")
        # A graph checkpoint thread is scoped to one Run. Conversational memory
        # is scoped independently to the Session so completed Pipeline control
        # fields never leak into the next user turn.
        thread_id = run_id

        # Persist input as a quarantined Artifact.
        # Persist the complete request as one opaque ingress payload. Kernel does
        # not inspect or split business fields and content parts; PreRecall owns
        # that normalization.
        # ``input`` is declared as BaseModel so ingress can accept any business
        # schema. Serialize the concrete subtype instead of BaseModel's empty
        # declared shape.
        input_bytes = request.model_dump_json(serialize_as_any=True).encode()
        owner = ArtifactOwner(tenant_id=self._tenant, erasure_scope_id=_new_id("scope"))

        async def _payload():
            yield input_bytes

        input_ref = await self._ports.artifact_manager.put(
            operation_id=_new_id("op"), owner=owner, lineage=[], payload=_payload(),
            purpose="run_execution",
        )
        # Atomic create (dev: sequential store calls; real impl = one DB transaction).
        record = RunStartRequestRecord(
            tenant_id=self._tenant, idempotency_namespace=namespace,
            request_idempotency_key=request.request_idempotency_key, scenario_key=request.scenario_key,
            client_request_fingerprint=client_fp, execution_bundle_fingerprint=bundle_fp,
            run_id=run_id, input_metadata_ref=input_ref, status="pending", created_at=_now(),
        )
        stored_record = await self._ports.run_start_request_store.insert(record)
        # Another process may win the idempotency-key race after the initial
        # lookup. Only the winner may create control state or drive the graph.
        if stored_record.run_id != run_id:
            return await self._await_existing_handle(stored_record.run_id)

        snapshot = ExecutionContextSnapshot(
            run_id=run_id, thread_id=thread_id, scenario_key=request.scenario_key,
            original_principal=auth_context.principal, tenant_id=self._tenant,
            authorization_scope=auth_context.authorization_scope, created_at=_now(),
            absolute_deadline=request.options.absolute_deadline,
            bundle_checksum=scenario.checksum, graph_checksum=scenario.checksum,
            application_version=self._runtime.settings.service_version,
        )
        await self._ports.execution_context_store.insert(snapshot)

        state = RunControlState(
            run_id=run_id, tenant_id=self._tenant, version=0, lease_version=0,
            run_status="running", lease_owner="usagi-worker",
            lease_expires_at=_now() + timedelta(seconds=_LEASE_TTL_SECONDS),
            fencing_token=1, budget_used=BudgetUsage(),
        )
        await self._ports.run_control_store.create(state)

        bind_thread = getattr(self._ports.checkpointer, "abind_thread", None)
        if bind_thread is not None:
            await bind_thread(
                tenant_id=self._tenant,
                thread_id=thread_id,
                control_id=run_id,
                graph_checksum=scenario.checksum,
            )

        await self._ports.outbox_store.enqueue(DurableOutboxEvent(
            event_id=_new_id("evt"), tenant_id=self._tenant, aggregate_type="run",
            aggregate_id=run_id, aggregate_version=0, event_type="run.start_requested",
            dedup_key=f"start:{run_id}", available_at=_now(), created_at=_now(),
        ))

        # Drive the compiled graph (dev: inline worker; production: separate Worker).
        with span(
            self._obs,
            "workflow.run",
            **{
                "usagi.workflow.operation": "start",
                "usagi.scenario.name": request.scenario_key,
            },
        ) as telemetry:
            await self._drive(
                run_id, scenario, input_ref, request, auth_context,
                thread_id, session_id, output_contract,
            )
            final_state = await self._ports.run_control_store.get(run_id)
            if final_state is not None:
                telemetry.set_outcome(final_state.run_status)

        return await self._build_handle(run_id)

    async def _drive(
        self, run_id: str, scenario, input_ref: ArtifactRef,
        request: RunStartRequest, auth_context: AuthContext, thread_id: str,
        session_id: str, output_contract: OutputContract | None,
    ) -> None:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} vanished during drive")
        gate = FencingGate(
            tenant_id=self._tenant, control_kind="run", control_id=run_id,
            lease_owner=state.lease_owner or "", fencing_token=state.fencing_token,
        )
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "tenant_id": self._tenant,
                "fencing_gate": gate,
                "checkpoint_ns": "",
                "run_context": RunContext(
                    run_id=run_id,
                    thread_id=thread_id,
                    tenant_id=self._tenant,
                    principal=auth_context.principal,
                    authorization_scope=auth_context.authorization_scope,
                    deadline=request.options.absolute_deadline,
                    fencing_token=state.fencing_token,
                    session_id=session_id,
                    output_contract=output_contract,
                ),
            }
        }
        # Pass one opaque request reference into the graph. PreRecall owns all
        # interpretation of business fields, modalities and request options.
        input_state: dict[str, Any] = {
            "request_ref": input_ref.artifact_id,
            "run_id": run_id,
        }
        if output_contract is not None:
            input_state.update({
                "output_schema_checksum": output_contract.schema_checksum,
                "structured_output_attempts": 0,
            })

        heartbeat = asyncio.create_task(
            self._keep_lease_alive(run_id, state.lease_owner or "", state.fencing_token)
        )
        try:
            result = await scenario.compiled_graph.ainvoke(input_state, config)
        except Exception as exc:
            log.exception("run %s failed while invoking the pipeline", run_id)
            reason = [type(exc).__name__]
            await self._rcm.to_failed(run_id, reason)
            return
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

        snapshot = await self._ports.execution_context_store.get(run_id)
        assert snapshot is not None
        await self._finish_graph_result(run_id, scenario, config, result, snapshot)

    async def _keep_lease_alive(
        self, run_id: str, owner: str, fencing_token: int
    ) -> None:
        """Renew the run lease while the graph executes.

        Long model calls can exceed one lease TTL between checkpoint writes;
        without renewal the fenced checkpointer rejects the next write. The
        checkpointer stays the final arbiter: if the lease is genuinely lost
        (cancellation, takeover), renewal stops and the gate rejects the write.
        """
        interval = max(1.0, _LEASE_TTL_SECONDS / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._lease.renew(run_id, owner, fencing_token)
            except (LeaseLost, UsagiError):
                return

    async def _finish_graph_result(
        self, run_id: str, scenario, config: dict[str, Any], result: Any,
        snapshot: ExecutionContextSnapshot,
    ) -> None:
        state_config = config
        configurable = dict(config.get("configurable", {}))
        if "checkpoint_id" in configurable:
            configurable.pop("checkpoint_id")
            state_config = {**config, "configurable": configurable}
        gstate = await scenario.compiled_graph.aget_state(state_config)
        interrupts = self._collect_interrupts(gstate)
        pending = await self._ports.approval_store.list_pending(run_id)
        # A resumed node can interrupt again before producing a new checkpoint
        # (for example, a second tool in the same model response). Pending
        # approval state is therefore authoritative even when ``next`` is empty.
        if gstate.next or interrupts or pending:
            checkpoint_id = (gstate.config.get("configurable") or {}).get("checkpoint_id", "")
            interrupt_id = "interrupt"
            if interrupts:
                value = interrupts[0].value
                interrupt_id = (
                    str(value.get("approval_id", interrupts[0].id))
                    if isinstance(value, dict) else interrupts[0].id
                )
            elif len(pending) == 1:
                interrupt_id = pending[0].interrupt_id
            await self._rcm.to_suspended(run_id, checkpoint_id, interrupt_id)
            return

        state_values = getattr(gstate, "values", None)
        terminal = state_values if isinstance(state_values, dict) else result
        if isinstance(terminal, dict) and terminal.get("pass_disposition") in {
            "run_failed", "next_pass"
        }:
            reasons = terminal.get("reason_codes")
            if not isinstance(reasons, list) or not reasons:
                reasons = [
                    "structured_output.retry_exhausted"
                    if terminal.get("output_schema_checksum")
                    else "pipeline.run_failed"
                ]
            await self._rcm.to_failed(run_id, [str(reason) for reason in reasons])
            return
        final_ref = self._extract_final_ref(terminal)
        if final_ref is None and terminal is not result:
            final_ref = self._extract_final_ref(result)
        if final_ref is None:
            await self._rcm.to_failed(run_id, ["pipeline.missing_final_output"])
            return
        await self._rcm.to_completed(run_id, final_ref)

    @classmethod
    def _collect_interrupts(cls, snapshot) -> list:
        interrupts = list(getattr(snapshot, "interrupts", ()))
        for task in getattr(snapshot, "tasks", ()):
            interrupts.extend(getattr(task, "interrupts", ()))
            nested = getattr(task, "state", None)
            if hasattr(nested, "tasks"):
                interrupts.extend(cls._collect_interrupts(nested))
        return interrupts

    @staticmethod
    def _extract_final_ref(result: Any) -> ArtifactRef | None:
        if isinstance(result, dict):
            for key in ("final_output_ref", "result_ref"):
                val = result.get(key)
                if isinstance(val, str) and val:
                    return ArtifactRef(artifact_id=val, content_type="application/octet-stream")
                if isinstance(val, ArtifactRef) and val.artifact_id:
                    return val
        return None

    async def _build_handle(self, run_id: str) -> RunHandle:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            raise UsagiError(f"run_control {run_id} not found after start")
        snapshot = await self._ports.execution_context_store.get(run_id)
        if snapshot is None:
            raise UsagiError(f"execution context {run_id} not found after start")
        outcome = self._project(run_id, state)
        return RunHandle(
            run_id=run_id,
            thread_id=snapshot.thread_id,
            scenario_key=snapshot.scenario_key,
            outcome=outcome, created_at=_now(),
        )

    async def _await_existing_handle(self, run_id: str) -> RunHandle:
        """Wait briefly for the winning starter to publish its durable state."""
        for _ in range(500):
            try:
                return await self._build_handle(run_id)
            except UsagiError:
                await asyncio.sleep(0.01)
        raise UsagiError(f"idempotent run {run_id} was not initialized")

    async def _session_message(
        self, session_id: str, handle: RunHandle, *, approval=None
    ) -> SessionMessage:
        outcome = handle.outcome
        structured_output = None
        if isinstance(outcome, Suspended):
            if approval is None:
                tasks = await self._ports.approval_store.list_pending(handle.run_id)
                approval = tasks[0] if len(tasks) == 1 else None
            tool = approval.tool_name if approval is not None else "unknown"
            message = (
                f"₍ᐢ..ᐢ₎ USAGI想使用{tool}工具，您同意吗(☆▽☆)，"
                "请回复“Yes”或“No”！"
            )
            status = "pending_approval"
        elif isinstance(outcome, Completed):
            stream = await self._ports.artifact_manager.get(
                outcome.result_ref, "run_execution"
            )
            raw = b"".join([part async for part in stream])
            message = raw.decode(
                "utf-8", errors="replace"
            )
            snapshot = await self._ports.execution_context_store.get(handle.run_id)
            output_contract = None
            if snapshot is not None:
                scenario = self._runtime.scenario_registry.get(snapshot.scenario_key)
                output_contract = self._runtime.agent_manager.output_contract(
                    scenario.config.agent_id
                )
            if output_contract is not None:
                try:
                    from usagi_agent.types.action import ToolObservation
                    observation = ToolObservation.model_validate_json(raw)
                    candidate = (observation.output or {}).get("structured_output")
                    if isinstance(candidate, dict):
                        structured_output = candidate
                        display = candidate.get("message")
                        message = display if isinstance(display, str) else json.dumps(
                            candidate, ensure_ascii=False
                        )
                except (ValueError, TypeError):
                    message = "structured output result is corrupted"
            status = "idle"
        elif isinstance(outcome, Failed):
            message = "会话处理失败：" + ", ".join(outcome.reason_codes)
            status = "idle"
        else:
            message = f"会话状态：{outcome.kind}"
            status = "idle"
        if not isinstance(outcome, Completed):
            structured_output = None
        await self._runtime.session_manager.set_status(session_id, status)
        return SessionMessage(
            session_id=session_id,
            message=message,
            run_id=handle.run_id,
            outcome=outcome,
            structured_output=structured_output,
        )

    def _resolve_auth(self, auth: AuthContext | None) -> AuthContext:
        if auth is None:
            return AuthContext(
                principal=self._principal,
                authorization_scope=("run.start", "run.execute"),
            )
        if not isinstance(auth, AuthContext):
            raise TypeError("auth must be an AuthContext")
        return auth

    def _new_session_id(self, auth: AuthContext, idempotency_key: str) -> str:
        identity = [
            self._tenant,
            auth.principal.model_dump(mode="json"),
            idempotency_key,
        ]
        return "session_" + hashlib.sha256(_canonical(identity).encode()).hexdigest()

    async def _authorize_run(
        self, run_id: str, auth: AuthContext | None, permission: str
    ) -> None:
        if auth is None:
            return
        context = self._resolve_auth(auth)
        snapshot = await self._ports.execution_context_store.get(run_id)
        if snapshot is None:
            raise UsagiError(f"run {run_id} not found")
        if context.principal != snapshot.original_principal and "run.admin" not in context.authorization_scope:
            raise PolicyDeniedError("caller does not own this run")
        if permission not in context.authorization_scope and "run.admin" not in context.authorization_scope:
            raise PolicyDeniedError(f"missing authorization scope: {permission}")

    def _make_resume_token(self, run_id: str, interrupt_id: str, checkpoint_id: str) -> str:
        payload = f"{run_id}:{interrupt_id}:{checkpoint_id}".encode()
        configured = self._runtime.settings.resume_hmac_key
        key = configured.get_secret_value().encode() if configured else _DEV_HMAC_KEY
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def _project(self, run_id: str, state: RunControlState) -> RunOutcome:
        status = state.run_status
        if status == "running":
            return Running(run_id=run_id, started_at=_now(), scenario_key="")
        if status == "suspended":
            interrupt_id = state.interrupt_set_digest or "interrupt"
            return Suspended(
                run_id=run_id, checkpoint_id=state.suspended_checkpoint_id or "",
                reason_code="interrupt", interrupts=[InterruptDescriptor(
                    interrupt_id=interrupt_id,
                    kind="approval",
                    checkpoint_id=state.suspended_checkpoint_id or "",
                    token_delivery="reissue_required",
                )],
            )
        if status == "resume_accepted":
            # Projected as Resuming.
            return Running(run_id=run_id, started_at=_now(), scenario_key="")  # simplified
        if status == "cancel_requested":
            from usagi_agent.types.run import Cancelling

            return Cancelling(
                run_id=run_id, requested_at=state.cancel_requested_at or _now(),
                reason_code=state.cancellation_reason_code or CancellationReasonCode.USER_REQUEST,
                in_flight_operations=0, unresolved_operations=0,
            )
        if status == "completed":
            ref = state.final_result_ref
            if ref is None or not ref.artifact_id:
                return Failed(
                    run_id=run_id,
                    failed_at=_now(),
                    reason_codes=["pipeline.missing_final_output"],
                )
            return Completed(run_id=run_id, completed_at=state.cancelled_at or _now(), result_ref=ref)
        if status == "failed":
            return Failed(
                run_id=run_id, failed_at=_now(),
                reason_codes=state.reason_codes or ["run.failed"],
            )
        if status == "cancelled":
            from usagi_agent.types.run import Cancelled

            return Cancelled(
                run_id=run_id, cancelled_at=state.cancelled_at or _now(),
                reason_code=state.cancellation_reason_code or CancellationReasonCode.USER_REQUEST,
            )
        return Running(run_id=run_id, started_at=_now(), scenario_key="")
