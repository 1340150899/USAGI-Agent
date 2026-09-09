"""Persistent user/session records used by the conversational runtime."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

SessionStatus = Literal["idle", "running", "pending_approval", "closed"]


class UserRecord(BaseModel):
    id: int | None = None
    uid: str
    uptime: datetime
    crtime: datetime


class SessionRecord(BaseModel):
    id: str
    uid: str
    status: SessionStatus = "idle"
    uptime: datetime
    crtime: datetime


@runtime_checkable
class SessionStore(Protocol):
    async def create(self, uid: str, session_id: str) -> SessionRecord: ...
    async def get(self, session_id: str) -> SessionRecord | None: ...
    async def get_for_uid(self, uid: str) -> SessionRecord | None: ...
    async def set_status(self, session_id: str, status: SessionStatus) -> SessionRecord: ...
