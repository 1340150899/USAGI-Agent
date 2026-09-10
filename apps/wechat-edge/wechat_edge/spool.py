"""In-memory observation buffer for the desktop WeChat collector.

The edge process deliberately owns no durable state. HTTP Server is the
durability boundary; a collector restart establishes a fresh visual baseline
instead of replaying local history.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from datetime import datetime, timezone


def overlap(previous, current):
    """Return the observed prefix length; a missing anchor is a gap, never a guess."""
    if not previous:
        return 0
    if previous == current:
        return len(current)
    for size in range(min(len(previous), len(current)), 0, -1):
        if previous[-size:] == current[:size]:
            anchor = current[:size]
            if sum(
                current[index : index + size] == anchor
                for index in range(len(current) - size + 1)
            ) != 1:
                return None
            return size
    return None


class MemorySpool:
    """Process-local snapshots and pending uploads; never writes SQLite or message logs."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshots: dict[str, dict[str, object]] = {}
        self._events: deque[tuple[str, str]] = deque()

    def capture(self, config, conversation, messages, *, rebaseline=False):
        signatures = [message["signature"] for message in messages]
        with self._lock:
            old = self._snapshots.get(conversation["ref"])
            if old is None or rebaseline:
                self._snapshots[conversation["ref"]] = {
                    "payload": signatures,
                    "epoch": uuid.uuid4().hex,
                    "seq": 0,
                    "blocked": False,
                }
                return 0
            if old["blocked"]:
                return 0
            start = overlap(old["payload"], signatures)
            previous_seq = old["seq"] if isinstance(old["seq"], int) else 0
            seq = previous_seq + 1
            gap = start is None
            selected = (
                [
                    {
                        "text": "采集窗口失去连续观察锚点，请人工核对并重新建立基线。",
                        "sender_ref": conversation["sender_ref"],
                        "direction": "incoming",
                    }
                ]
                if gap
                else messages[start:]
            )
            for index, message in enumerate(selected):
                event_id = f"{old['epoch']}:{seq}:{index}"
                payload = {
                    "schema_version": 1,
                    "source_stream_id": config["account_ref"]
                    + ":"
                    + conversation["ref"],
                    "source_event_ref": event_id,
                    "source_cursor_ref": event_id,
                    "ordering_mode": "observation_ordered",
                    "account_ref": config["account_ref"],
                    "conversation_ref": conversation["ref"],
                    "sender_ref": message["sender_ref"],
                    "direction": message["direction"],
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "source_gap": gap,
                    "content_parts": [
                        {"type": "text", "text": message.get("text", "")}
                    ],
                    "_image_path": message.get("image_path"),
                }
                self._events.append(
                    (event_id, json.dumps(payload, ensure_ascii=False))
                )
            self._snapshots[conversation["ref"]] = {
                "payload": signatures,
                "epoch": old["epoch"],
                "seq": seq,
                "blocked": gap,
            }
            return len(selected)

    def pending(self):
        with self._lock:
            return list(self._events)[:20]

    def ack(self, event_id):
        with self._lock:
            if self._events and self._events[0][0] == event_id:
                self._events.popleft()
                return
            self._events = deque(
                event for event in self._events if event[0] != event_id
            )


# Compatibility name for callers; the implementation is intentionally memory-only.
Spool = MemorySpool
