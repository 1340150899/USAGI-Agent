"""SQLite implementation of the deliberately small session store."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

from usagi_agent.persistence.ports.session import SessionRecord, SessionStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqliteSessionStore:
    def __init__(self, path: str) -> None:
        self.path = path

    @staticmethod
    def _record(row: sqlite3.Row | None) -> SessionRecord | None:
        if row is None:
            return None
        return SessionRecord(
            id=row["id"], uid=row["uid"], status=row["status"],
            uptime=datetime.fromisoformat(row["uptime"]),
            crtime=datetime.fromisoformat(row["crtime"]),
        )

    async def create(self, uid: str, session_id: str) -> SessionRecord:
        def operation() -> SessionRecord:
            now = _now().isoformat()
            with sqlite3.connect(self.path, timeout=30) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR IGNORE INTO users(uid,uptime,crtime) VALUES (?,?,?)",
                    (uid, now, now),
                )
                db.execute("UPDATE users SET uptime=? WHERE uid=?", (now, uid))
                db.execute(
                    "INSERT OR IGNORE INTO sessions(id,uid,status,uptime,crtime) VALUES (?,?,?,?,?)",
                    (session_id, uid, "idle", now, now),
                )
                row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                record = self._record(row)
                if record is None or record.uid != uid:
                    raise PermissionError(f"session {session_id!r} belongs to another user")
                return record
        return await asyncio.to_thread(operation)
    async def get(self, session_id: str) -> SessionRecord | None:
        def operation() -> SessionRecord | None:
            with sqlite3.connect(self.path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                return self._record(row)
        return await asyncio.to_thread(operation)

    async def get_for_uid(self, uid: str) -> SessionRecord | None:
        def operation() -> SessionRecord | None:
            with sqlite3.connect(self.path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM sessions WHERE uid=? AND status!='closed' ORDER BY crtime DESC LIMIT 1",
                    (uid,),
                ).fetchone()
                return self._record(row)
        return await asyncio.to_thread(operation)

    async def set_status(self, session_id: str, status: SessionStatus) -> SessionRecord:
        def operation() -> SessionRecord:
            now = _now().isoformat()
            with sqlite3.connect(self.path, timeout=30) as db:
                db.row_factory = sqlite3.Row
                result = db.execute(
                    "UPDATE sessions SET status=?,uptime=? WHERE id=?", (status, now, session_id)
                )
                if result.rowcount != 1:
                    raise KeyError(f"session {session_id!r} not found")
                row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                return self._record(row)  # type: ignore[return-value]
        return await asyncio.to_thread(operation)
