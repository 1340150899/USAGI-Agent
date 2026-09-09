"""Application-owned configuration for Algovate's xhs-mcp server."""

from __future__ import annotations

import os
from pathlib import Path

from usagi_agent.tools import MCPServerConfig

XHS_MCP_PACKAGE = "xhs-mcp@0.8.13"

try:
    from usagi_httpserver.tool_specs import SAFE_XHS_TOOLS, WRITE_XHS_TOOLS, XHS_TOOL_SPECS
except ModuleNotFoundError as exc:
    if exc.name != "usagi_httpserver":
        raise
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "httpserver"))
    from usagi_httpserver.tool_specs import SAFE_XHS_TOOLS, WRITE_XHS_TOOLS, XHS_TOOL_SPECS


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
    overrides = {name: dict(spec) for name, spec in XHS_TOOL_SPECS.items()}

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
