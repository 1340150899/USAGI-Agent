from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from usagi_agent.policies.engine import DefaultPolicyEngine
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.tool import ToolSpec

HTTP_APP_ROOT = Path(__file__).parents[3] / "apps" / "httpserver"
if str(HTTP_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(HTTP_APP_ROOT))

from usagi_httpserver import xhs_mcp


class _ToolCatalog:
    def __init__(self, specs: dict[str, ToolSpec]) -> None:
        self._specs = specs

    def get_spec(self, name: str) -> ToolSpec:
        return self._specs[name]

    def get_specs(self, names: Iterable[str]) -> tuple[ToolSpec, ...]:
        return tuple(self.get_spec(name) for name in names)


def test_default_xhs_mcp_config_only_exposes_read_tools():
    config = xhs_mcp.build_xhs_mcp_config()

    assert config.transport == "streamable_http"
    assert config.url == "http://127.0.0.1:18060/mcp"
    assert config.command is None
    assert config.args == ()
    assert config.effective_prefix == "xhs_"
    assert config.enabled_tools == xhs_mcp.SAFE_XHS_TOOLS
    assert "publish_content" not in config.enabled_tools
    assert all(
        config.spec_overrides[name]["risk"] == "read"
        for name in xhs_mcp.SAFE_XHS_TOOLS
    )


def test_write_profile_authorizes_writes_without_automatic_retries():
    config = xhs_mcp.build_xhs_mcp_config(allow_writes=True)

    assert "publish_content" in config.enabled_tools
    for name in xhs_mcp.WRITE_XHS_TOOLS:
        override = config.spec_overrides[name]
        assert override["risk"] == "write"
        assert override["write_safety"] == "at_most_once_manual"
        assert override.get("max_retries", 0) == 0


def test_http_xhs_mcp_config_does_not_mix_stdio_fields():
    config = xhs_mcp.build_xhs_mcp_config(url="http://127.0.0.1:18061/mcp")

    assert config.transport == "streamable_http"
    assert config.url == "http://127.0.0.1:18061/mcp"
    assert config.command is None
    assert config.args == ()


@pytest.mark.asyncio
async def test_xhs_write_profile_does_not_require_policy_approval():
    config = xhs_mcp.build_xhs_mcp_config(allow_writes=True)
    catalog = _ToolCatalog({
        name: ToolSpec.model_validate({
            "name": name, "description": "test", **config.spec_overrides[name],
        })
        for name in xhs_mcp.WRITE_XHS_TOOLS
    })
    policy = DefaultPolicyEngine(catalog)
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="test-user")
    context = ToolContext(execution=GovernedExecutionContext(
        tenant_id="test", principal=principal, authorization_scope=(),
        control_kind="run", control_id="test-xhs-policy", fencing_token=1,
    ))
    for name in xhs_mcp.WRITE_XHS_TOOLS:
        decision = await policy.evaluate(
            principal=principal, action="tool.execute", tool_name=name,
            arguments={}, context=context,
        )
        assert decision.effect == "allow"
