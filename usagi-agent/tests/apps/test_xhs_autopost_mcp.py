from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from collections.abc import Iterable

import pytest

from usagi_agent.policies.engine import DefaultPolicyEngine
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.tool import ToolSpec


class _ToolCatalog:
    def __init__(self, specs: dict[str, ToolSpec]) -> None:
        self._specs = specs

    def get_spec(self, name: str) -> ToolSpec:
        return self._specs[name]

    def get_specs(self, names: Iterable[str]) -> tuple[ToolSpec, ...]:
        return tuple(self.get_spec(name) for name in names)


def _load_app_mcp_module():
    path = (
        Path(__file__).parents[3]
        / "apps"
        / "xhs-autopost"
        / "xhs_autopost"
        / "mcp.py"
    )
    spec = importlib.util.spec_from_file_location("xhs_autopost_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_xhs_mcp_config_only_exposes_read_tools():
    module = _load_app_mcp_module()
    config = module.build_xhs_mcp_config(cwd=Path.cwd())

    assert config.transport == "stdio"
    assert config.command == ("npx.cmd" if os.name == "nt" else "npx")
    assert config.args == ("--yes", "xhs-mcp@0.8.13", "mcp")
    assert config.effective_prefix == ""
    assert config.enabled_tools == module.SAFE_XHS_TOOLS
    assert "xhs_publish_content" not in config.enabled_tools
    assert all(
        config.spec_overrides[name]["risk"] == "read"
        for name in module.SAFE_XHS_TOOLS
    )


def test_write_profile_authorizes_writes_without_automatic_retries():
    module = _load_app_mcp_module()
    config = module.build_xhs_mcp_config(allow_writes=True)

    assert "xhs_publish_content" in config.enabled_tools
    for name in module.WRITE_XHS_TOOLS:
        override = config.spec_overrides[name]
        assert override["risk"] == "write"
        assert override["write_safety"] == "at_most_once_manual"
        assert override.get("max_retries", 0) == 0


def test_http_xhs_mcp_config_does_not_mix_stdio_fields():
    module = _load_app_mcp_module()
    config = module.build_xhs_mcp_config(url="http://127.0.0.1:3000/mcp")

    assert config.transport == "streamable_http"
    assert config.url == "http://127.0.0.1:3000/mcp"
    assert config.command is None
    assert config.args == ()


@pytest.mark.asyncio
async def test_xhs_write_profile_does_not_require_policy_approval():
    module = _load_app_mcp_module()
    config = module.build_xhs_mcp_config(allow_writes=True)
    catalog = _ToolCatalog({
        name: ToolSpec.model_validate({
            "name": name, "description": "test", **config.spec_overrides[name],
        })
        for name in module.WRITE_XHS_TOOLS
    })
    policy = DefaultPolicyEngine(catalog)
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="test-user")
    context = ToolContext(execution=GovernedExecutionContext(
        tenant_id="test", principal=principal, authorization_scope=(),
        control_kind="run", control_id="test-xhs-policy", fencing_token=1,
    ))
    for name in module.WRITE_XHS_TOOLS:
        decision = await policy.evaluate(
            principal=principal, action="tool.execute", tool_name=name,
            arguments={}, context=context,
        )
        assert decision.effect == "allow"
