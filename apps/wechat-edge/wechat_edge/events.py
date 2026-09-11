"""Message classification and ingress payload construction (stdlib-only).

Mirrors the ingestion contract the HTTP Server already enforces: the event
schema is frozen (``extra='forbid'``), ``source_stream_id`` must stay
``{account_ref}:{conversation_ref}``, and ``ordering_mode`` must stay the
literal ``observation_ordered``. Event ids are stable
``{username}:{sort_seq}`` strings so retries are idempotent server-side.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# Group messages carry the sender wxid as a "wxid_xxx:\n" content prefix;
# self messages in a group carry the chatroom id instead (4.x real_sender_id
# is unreliable in groups). Mirrors wechatauto's own stripping regex.
GROUP_SENDER_RE = re.compile(r'^(wxid_[0-9a-zA-Z_-]+|[^:\n]{1,120}@chatroom):\s*')


def event_ref(username: str, sort_seq: int) -> str:
    return f'{username}:{sort_seq}'


def _is_self(msg: dict, self_wxid: str) -> bool:
    sender_id = msg.get('sender_id')
    return sender_id == 2 or bool(self_wxid and str(sender_id) == str(self_wxid))


def classify(msg: dict, conversation: dict, self_wxid: str) -> dict | None:
    """Map a Listener message dict to an event skeleton, or None to skip.

    ``conversation`` carries username/ref/sender_ref plus ``self_ref``
    (injected from the top-level config by the resolver) and an optional
    ``sender_map``. Only 文本/图片 are collected (unknown local_type
    arrives as an int). Group senders resolve through ``sender_map`` when
    provided, falling back to the conversation-level sender_ref so unknown
    members are still attributed (both are inside the server binding).
    """
    mtype = msg.get('type')
    if mtype == '文本':
        kind = 'text'
    elif mtype == '图片':
        kind = 'image'
    else:
        return None

    text = str(msg.get('content') or '')
    sender_wxid = None
    outgoing = _is_self(msg, self_wxid)
    if conversation['username'].endswith('@chatroom'):
        match = GROUP_SENDER_RE.match(text)
        if match:
            text = text[match.end():]
            prefix = match.group(1)
            if prefix.endswith('@chatroom'):
                outgoing = True
            else:
                sender_wxid = prefix

    if outgoing:
        sender_ref = conversation['self_ref']
    elif sender_wxid is not None:
        sender_map = conversation.get('sender_map') or {}
        sender_ref = sender_map.get(sender_wxid, conversation['sender_ref'])
    else:
        sender_ref = conversation['sender_ref']

    return {
        'kind': kind,
        'text': text,
        'sender_wxid': sender_wxid,
        'sender_ref': sender_ref,
        'direction': 'outgoing' if outgoing else 'incoming',
    }


def build_payload(*, config: dict, conversation: dict, msg: dict,
                  item: dict, image_path: str | None) -> dict:
    """Construct the ingress event payload; ``_image_path`` is popped by
    the upload loop, which appends the image content part after the media
    upload succeeds. A failed image download degrades to a text placeholder
    so downstream knows an image existed."""
    if item['kind'] == 'image' and image_path is None:
        text = '[图片采集失败]'
    else:
        text = item['text']
    ref = event_ref(msg['username'], msg['sort_seq'])
    return {
        'schema_version': 1,
        'source_stream_id': config['account_ref'] + ':' + conversation['ref'],
        'source_event_ref': ref,
        'source_cursor_ref': ref,
        'ordering_mode': 'observation_ordered',
        'account_ref': config['account_ref'],
        'conversation_ref': conversation['ref'],
        'sender_ref': item['sender_ref'],
        'direction': item['direction'],
        'occurred_at': datetime.fromtimestamp(
            int(msg['create_time']), tz=timezone.utc).isoformat(),
        'source_gap': False,
        'content_parts': [{'type': 'text', 'text': text}],
        '_image_path': image_path,
    }
