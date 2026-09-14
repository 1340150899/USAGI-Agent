"""Kernel cross-cutting managers.

These are *execution* helpers (constructed at Bootstrap, methods run at Run time). They are
deliberately thin: the heavy state-machine work lives in the Stores; the managers compose
Port calls and enforce invariants at node/external-call boundaries.
"""
from __future__ import annotations

from datetime import datetime, timezone

from usagi_agent.api.errors import BudgetExceededError, FencingGateError
from usagi_agent.persistence.backend import InfrastructurePorts
from usagi_agent.types.budget import Budget, BudgetUsage
from usagi_agent.types.settlement import FencingGate


class BudgetManager:
    """Checks hard ceilings against the UsageLedger projection."""

    def __init__(self, ports: InfrastructurePorts) -> None:
        self._ports = ports

    async def check(self, tenant_id: str, run_id: str, ceiling: Budget) -> None:
        usage = await self._ports.usage_ledger.get_budget_usage(tenant_id, run_id)
        if ceiling.max_total_cost is not None and (usage.settled_cost + usage.reserved_cost) > ceiling.max_total_cost:
            raise BudgetExceededError("total cost ceiling exceeded")
        if ceiling.max_input_tokens is not None and usage.input_tokens > ceiling.max_input_tokens:
            raise BudgetExceededError("input token ceiling exceeded")
        if ceiling.max_output_tokens is not None and usage.output_tokens > ceiling.max_output_tokens:
            raise BudgetExceededError("output token ceiling exceeded")

    async def record_pass(self, tenant_id: str, run_id: str) -> None:
        from usagi_agent.types.settlement import UsageFact, UsageIdentityLink

        fact = UsageFact(
            usage_event_id=f"uf_{run_id}_pass",
            kind="pass", amount=1, unit="pass", phase="settled",
            occurred_at=datetime.now(timezone.utc),
        )
        link = UsageIdentityLink(
            usage_event_id=fact.usage_event_id, tenant_id=tenant_id,
            erasure_scope_id="default", usage_key_hmac=f"pass:{run_id}",
            run_id=run_id, source_id=run_id,
        )
        await self._ports.usage_ledger.append(fact, link)


class CancellationManager:
    """Cooperative cancel/deadline check at node entry + external reserve boundaries."""

    def __init__(self, ports: InfrastructurePorts) -> None:
        self._ports = ports

    async def checkpoint(self, run_id: str, gate: FencingGate | None, deadline: datetime | None) -> None:
        state = await self._ports.run_control_store.get(run_id)
        if state is None:
            return
        if state.run_status in ("cancel_requested", "cancelled", "failed"):
            raise FencingGateError(f"run {run_id} not progressing: {state.run_status}")
        if deadline and deadline <= datetime.now(timezone.utc):
            raise FencingGateError(f"run {run_id} deadline exceeded")
        if gate is not None:
            if state.fencing_token != gate.fencing_token:
                raise FencingGateError(f"fencing token mismatch for {run_id}")


class MiddlewareChain:
    """Ordered pre-node checks: cancel -> deadline -> budget -> fencing."""

    def __init__(self, budget: BudgetManager, cancellation: CancellationManager) -> None:
        self._budget = budget
        self._cancel = cancellation

    async def before_node(self, run_id: str, tenant_id: str, gate: FencingGate | None,
                         deadline: datetime | None, ceiling: Budget | None) -> None:
        await self._cancel.checkpoint(run_id, gate, deadline)
        if ceiling is not None:
            await self._budget.check(tenant_id, run_id, ceiling)


class ErrorMapper:
    """Maps internal errors to structured SafeError / reason codes."""

    @staticmethod
    def to_reason(exc: Exception) -> list[str]:
        from usagi_agent.api.errors import UsagiError

        if isinstance(exc, UsagiError):
            return [exc.reason_code]
        return [type(exc).__name__]


class EventPublisher:
    """Publishes lifecycle events via the outbox (transactional, )."""

    def __init__(self, ports: InfrastructurePorts) -> None:
        self._ports = ports

    async def publish(self, run_id: str, tenant_id: str, event_type: str) -> None:
        from usagi_agent.persistence.ports.outbox import DurableOutboxEvent

        await self._ports.outbox_store.enqueue(DurableOutboxEvent(
            event_id=f"evt_{run_id}_{event_type}", tenant_id=tenant_id,
            aggregate_type="run", aggregate_id=run_id, aggregate_version=0,
            event_type=event_type, dedup_key=f"{event_type}:{run_id}",
            available_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
        ))


class LifecycleManager:
    """Tracks run lifecycle transitions for observability (no authoritative state)."""

    def __init__(self) -> None:
        self._transitions: list[tuple[str, str, datetime]] = []

    def record(self, run_id: str, transition: str) -> None:
        self._transitions.append((run_id, transition, datetime.now(timezone.utc)))


class KernelComponents:
    """Bundle of cross-cutting managers produced by KernelInitializer (init only)."""

    def __init__(self, *, budget: BudgetManager, cancellation: CancellationManager,
                 middleware: MiddlewareChain, errors: ErrorMapper,
                 events: EventPublisher, lifecycle: LifecycleManager) -> None:
        self.budget = budget
        self.cancellation = cancellation
        self.middleware = middleware
        self.errors = errors
        self.events = events
        self.lifecycle = lifecycle
