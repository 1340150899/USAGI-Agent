"""In-memory upload queue for the desktop WeChat collector.

The edge process deliberately owns no durable state. HTTP Server is the
durability boundary; a collector restart establishes a fresh baseline from
the Listener watermark (registration-time latest sort_seq) instead of
replaying local history.
"""
from __future__ import annotations

import threading
from collections import deque


class Spool:
    """Process-local pending uploads; never writes SQLite or message logs.

    Producers are the per-conversation Listener worker threads; the single
    consumer is the upload loop. Event IDs are stable
    ``{username}:{sort_seq}`` strings, so a duplicate enqueue is dropped
    instead of being uploaded twice.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events: deque[tuple[str, str]] = deque()
        self._ids: set[str] = set()

    def enqueue(self, event_id: str, payload_json: str) -> bool:
        """Append an event; returns False when the id is already queued."""
        with self._lock:
            if event_id in self._ids:
                return False
            self._ids.add(event_id)
            self._events.append((event_id, payload_json))
            return True

    def pending(self, limit: int = 20) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._events)[:limit]

    def ack(self, event_id: str):
        with self._lock:
            if self._events and self._events[0][0] == event_id:
                self._events.popleft()
                self._ids.discard(event_id)
                return
            remaining = deque(
                event for event in self._events if event[0] != event_id
            )
            if len(remaining) != len(self._events):
                self._ids.discard(event_id)
            self._events = remaining

    def size(self) -> int:
        """True backlog count; unlike pending() this is not batch-truncated."""
        with self._lock:
            return len(self._events)
