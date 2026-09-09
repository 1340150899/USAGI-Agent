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
    "xhs_auth_status",
    "xhs_discover_feeds",
    "xhs_search_note",
    "xhs_get_note_detail",
    "xhs_get_user_notes",
)

WRITE_XHS_TOOLS = (
    "xhs_auth_login",
    "xhs_auth_logout",
    "xhs_comment_on_note",
    "xhs_delete_note",
    "xhs_publish_content",
)


def _read(*, requires_approval=True):
    return {"risk": "read", "requires_approval": requires_approval,
            "timeout_seconds": 120.0, "max_retries": 1}


def _write(*, requires_approval=True):
    return {"risk": "write", "requires_approval": requires_approval,
            "write_safety": "at_most_once_manual", "timeout_seconds": 300.0}


XHS_TOOL_SPECS = {
    "xhs_auth_status": _read(requires_approval=True),
    "xhs_discover_feeds": _read(requires_approval=True),
    "xhs_search_note": _read(requires_approval=True),
    "xhs_get_note_detail": _read(requires_approval=True),
    "xhs_get_user_notes": _read(requires_approval=True),
    "xhs_auth_login": _write(requires_approval=True),
    "xhs_auth_logout": _write(requires_approval=True),
    "xhs_comment_on_note": _write(requires_approval=True),
    "xhs_delete_note": _write(requires_approval=True),
    "xhs_publish_content": _write(requires_approval=True),
}
