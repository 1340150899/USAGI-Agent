"""Configuration for the Xiaohongshu MCP bundled under ``apps``."""

from __future__ import annotations

from usagi_agent.tools import MCPServerConfig

from .tool_specs import SAFE_XHS_TOOLS, WRITE_XHS_TOOLS, XHS_TOOL_SPECS

DEFAULT_XHS_MCP_URL = "http://127.0.0.1:18060/mcp"


def build_xhs_mcp_config(
    *,
    allow_writes: bool = False,
    url: str | None = None,
) -> MCPServerConfig:
    """Build a governed Streamable HTTP connection to the bundled service."""

    enabled_tools = SAFE_XHS_TOOLS + (WRITE_XHS_TOOLS if allow_writes else ())
    return MCPServerConfig(
        name="xhs",
        name_prefix="xhs_",
        transport="streamable_http",
        url=url or DEFAULT_XHS_MCP_URL,
        enabled_tools=enabled_tools,
        required_scopes=(),
        spec_overrides={name: dict(spec) for name, spec in XHS_TOOL_SPECS.items()},
        startup_timeout_seconds=180.0,
        read_timeout_seconds=300.0,
    )
