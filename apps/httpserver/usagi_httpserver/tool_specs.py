"""Single application catalog: every enabled tool and its approval decision.

Remote schemas are discovered from MCP; local governance is authoritative.
New upstream tools are not enabled unless listed here.
"""
from usagi_agent.tools.spec import BUILTIN_TOOL_SPECS

PYTHON_TOOL_SPECS = {
    "current_time": BUILTIN_TOOL_SPECS["current_time"].model_copy(update={"requires_approval": True}),
    "calculator": BUILTIN_TOOL_SPECS["calculator"].model_copy(update={"requires_approval": True}),
    "artifact_reader": BUILTIN_TOOL_SPECS["artifact_reader"].model_copy(update={"requires_approval": True}),
}

# These operations only retrieve data. Pinning their risk classification here is
# deliberate: MCP annotations are untrusted and can vary between server releases.
SAFE_XHS_TOOLS = (
    "check_login_status",
    "list_feeds",
    "search_feeds",
    "get_feed_detail",
    "user_profile",
)

WRITE_XHS_TOOLS = (
    "get_login_qrcode",
    "delete_cookies",
    "post_comment_to_feed",
    "reply_comment_in_feed",
    "publish_content",
    "publish_with_video",
    "like_feed",
    "favorite_feed",
)


def _read(*, requires_approval=True):
    return {"risk": "read", "requires_approval": requires_approval,
            "timeout_seconds": 120.0, "max_retries": 1}


def _write(*, requires_approval=True):
    return {"risk": "write", "requires_approval": requires_approval,
            "write_safety": "at_most_once_manual", "timeout_seconds": 300.0}


XHS_TOOL_SPECS = {
    "check_login_status": _read(requires_approval=True),
    "list_feeds": _read(requires_approval=True),
    "search_feeds": _read(requires_approval=True),
    "get_feed_detail": _read(requires_approval=True),
    "user_profile": _read(requires_approval=True),
    "get_login_qrcode": _write(requires_approval=True),
    "delete_cookies": _write(requires_approval=True),
    "post_comment_to_feed": _write(requires_approval=True),
    "reply_comment_in_feed": _write(requires_approval=True),
    "publish_content": _write(requires_approval=True),
    "publish_with_video": _write(requires_approval=True),
    "like_feed": _write(requires_approval=True),
    "favorite_feed": _write(requires_approval=True),
}
