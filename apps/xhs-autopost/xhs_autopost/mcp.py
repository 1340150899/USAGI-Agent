"""Application-owned configuration for Algovate's xhs-mcp server."""

from __future__ import annotations

import os
from pathlib import Path

from usagi_agent.tools import MCPServerConfig

XHS_MCP_PACKAGE = "xhs-mcp@0.8.13"

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


def _npx_command() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def build_xhs_mcp_config(
    *,
    allow_writes: bool = False,
    url: str | None = None,
    cwd: Path | None = None,
) -> MCPServerConfig:
    """Build a least-privilege xhs-mcp connection for one application runtime."""

    enabled_tools = SAFE_XHS_TOOLS + (WRITE_XHS_TOOLS if allow_writes else ())
    overrides: dict[str, dict[str, object]] = {
        name: {
            "risk": "read",
            "timeout_seconds": 120.0,
            "max_retries": 1,
        }
        for name in SAFE_XHS_TOOLS
    }
    # --allow-writes authorizes this test application's writes without a prompt.
    # Keep write semantics and at-most-once execution to avoid duplicate posts.
    overrides.update(
        {
            name: {
                "risk": "write",
                "write_safety": "at_most_once_manual",
                "timeout_seconds": 300.0,
            }
            for name in WRITE_XHS_TOOLS
        }
    )

    common = {
        "name": "xhs",
        # Upstream tool names already carry the xhs_ namespace.
        "name_prefix": "",
        "enabled_tools": enabled_tools,
        "required_scopes": (),
        "spec_overrides": overrides,
        "startup_timeout_seconds": 180.0,
        "read_timeout_seconds": 300.0,
    }
    if url is not None:
        return MCPServerConfig(
            transport="streamable_http",
            url=url,
            **common,
        )
    return MCPServerConfig(
        transport="stdio",
        command=_npx_command(),
        args=("--yes", XHS_MCP_PACKAGE, "mcp"),
        cwd=cwd,
        **common,
    )
