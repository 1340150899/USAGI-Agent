"""Transactional outbox + event bus Ports (design §24.2).

Business commits and event publishing stay consistent via the outbox. Payload may only be
a low-sensitivity envelope or ArtifactRef — never raw chat / tool params / route
destinations. Mandatory event classes (start, resume wakeup, due-case, key destruction,
reconciliation) never silently dead-letter.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import ArtifactRef

OutboxStatus = Literal["pending", "claimed", "published", "dead_letter", "cancelled"]


class DurableOutboxEvent(BaseModel):
    event_id: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    dedup_key: str
    payload_ref: ArtifactRef | None = None
    status: OutboxStatus = "pending"
    available_at: datetime
    attempt: int = 0
    version: int = 0
    claim_owner: str | None = None
    claim_expires_at: datetime | None = None
    last_reason_code: str | None = None
    created_at: datetime
    published_at: datetime | None = None


class InboxReceipt(BaseModel):
    tenant_id: str
    consumer: str
    event_id: str
    outcome: Literal["applied", "ignored", "failed_terminal"]
    applied_operation_id: str | None = None


@runtime_checkable
class OutboxStore(Protocol):
    async def enqueue(self, event: DurableOutboxEvent) -> DurableOutboxEvent: ...

    async def claim(self, *, consumer: str, now: datetime, lease_seconds: int) -> DurableOutboxEvent | None: ...

    async def publish(self, event_id: str, *, expected_version: int) -> DurableOutboxEvent: ...

    async def ack(self, consumer: str, event_id: str, receipt: InboxReceipt) -> InboxReceipt: ...


@runtime_checkable
class EventBus(Protocol):
    def subscribe(self, event_type: str) -> AsyncIterator[DurableOutboxEvent]: ...

    async def publish(self, event: DurableOutboxEvent) -> None: ...
