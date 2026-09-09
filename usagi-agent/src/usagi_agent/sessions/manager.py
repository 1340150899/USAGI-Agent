"""Creation, lookup and state updates for user-owned conversations."""
from uuid import uuid4

from usagi_agent.persistence.ports.session import SessionStatus, SessionStore


class SessionManager:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    async def create(self, uid: str, session_id: str | None = None):
        return await self.store.create(uid, session_id or f"session_{uuid4().hex}")

    async def get(self, session_id: str):
        return await self.store.get(session_id)

    async def get_for_uid(self, uid: str):
        return await self.store.get_for_uid(uid)

    async def set_status(self, session_id: str, status: SessionStatus):
        return await self.store.set_status(session_id, status)
