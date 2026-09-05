from __future__ import annotations

import base64
import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.policies.engine import DefaultPolicyEngine
from usagi_agent.tools import ToolManager
from usagi_agent.tools.mcp import MCPServerConfig, MCPServerSource, MCPToolSource
from usagi_agent.types.refs import PrincipalRef


class _Session:
    calls: list[tuple[str, dict[str, object]]]

    def __init__(self) -> None:
        self.calls = []

    async def list_tools(self, *, cursor: str | None = None):
        return {
            "tools": [
                {
                    "name": "screenshot",
                    "description": "Capture a page",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                }
            ]
        }

    async def call_tool(self, name: str, arguments: dict[str, object]):
        self.calls.append((name, arguments))
        return {
            "content": [
                {"type": "text", "text": "captured"},
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": base64.b64encode(b"fake-png").decode("ascii"),
                },
            ]
        }


def _context() -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="tenant",
            principal=PrincipalRef(
                principal_kind="user", principal_opaque_id="tester"
            ),
            authorization_scope=("browser.read",),
            control_kind="run",
            control_id="run-mcp",
            fencing_token=1,
        )
    )


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_for_port(port: int) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(f"HTTP MCP fixture did not listen on port {port}")


@pytest.mark.asyncio
async def test_mcp_source_discovers_governed_tool_and_persists_image_result():
    artifacts = InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )
    session = _Session()
    source = MCPToolSource(
        session,
        artifact_manager=artifacts,
        name_prefix="browser_",
        spec_overrides={
            "screenshot": {"required_scopes": ("browser.read",)}
        },
    )
    manager = ToolManager(artifact_manager=artifacts)
    adapters = await manager.load_source(source)
    assert len(adapters) == 1
    assert adapters[0].spec.name == "browser_screenshot"
    assert adapters[0].spec.adapter_kind == "mcp"

    observation = await manager.execute(
        name="browser_screenshot",
        arguments={"url": "https://example.test"},
        context=_context(),
        tool_call_id="call-1",
    )

    assert observation.status == "success"
    assert session.calls == [
        ("screenshot", {"url": "https://example.test"})
    ]
    assert len(observation.artifact_refs) == 1
    assert observation.artifact_refs[0].content_type == "image/png"
    assert observation.content_parts[0].type == "image"
    stream = await artifacts.get(
        observation.artifact_refs[0], "run_execution"
    )
    assert b"".join([chunk async for chunk in stream]) == b"fake-png"


@pytest.mark.asyncio
async def test_real_stdio_server_connects_discovers_executes_and_closes():
    artifacts = InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )
    fixture = Path(__file__).parents[2] / "scripts" / "mcp_fixture_server.py"
    config = MCPServerConfig(
        name="fixture",
        transport="stdio",
        command=sys.executable,
        args=(str(fixture),),
    )
    source = MCPServerSource(config, artifact_manager=artifacts)
    manager = ToolManager(artifact_manager=artifacts)
    try:
        adapters = await manager.load_source(source)
        assert [adapter.spec.name for adapter in adapters] == [
            "fixture__echo",
            "fixture__change_value",
        ]
        assert manager.get_spec("fixture__echo").risk == "read"
        assert manager.get_spec("fixture__change_value").risk == "high_risk_write"
        assert (
            manager.get_spec("fixture__change_value").write_safety
            == "at_most_once_manual"
        )

        observation = await manager.execute(
            name="fixture__echo",
            arguments={"message": "hello"},
            context=_context(),
            tool_call_id="real-call",
        )
        assert observation.status == "success"
        assert observation.output is not None
        assert observation.output["echo"] == "hello"
        assert (await source.health()) == "healthy"
    finally:
        await manager.shutdown()

    assert (await source.health()) == "unhealthy"


@pytest.mark.asyncio
async def test_real_streamable_http_server_connects_and_executes():
    artifacts = InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )
    fixture = Path(__file__).parents[2] / "scripts" / "mcp_fixture_server.py"
    port = _unused_local_port()
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(fixture),
        "--transport",
        "http",
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=creationflags,
    )
    manager = ToolManager(artifact_manager=artifacts)
    try:
        await _wait_for_port(port)
        source = MCPServerSource(
            MCPServerConfig(
                name="http_fixture",
                transport="streamable_http",
                url=f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": SecretStr("Bearer test-secret")},
            ),
            artifact_manager=artifacts,
        )
        await manager.load_source(source)
        observation = await manager.execute(
            name="http_fixture__echo",
            arguments={"message": "over-http"},
            context=_context(),
            tool_call_id="http-call",
        )
        assert observation.status == "success"
        assert observation.output is not None
        assert observation.output["echo"] == "over-http"
        assert await source.health() == "healthy"
    finally:
        await manager.shutdown()
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)


def test_mcp_server_config_rejects_mixed_transport_fields():
    with pytest.raises(ValueError, match="does not accept HTTP fields"):
        MCPServerConfig(
            name="bad",
            transport="stdio",
            command="python",
            url="https://example.test/mcp",
        )


@pytest.mark.asyncio
async def test_default_policy_requires_approval_for_untrusted_mcp_write():
    session = _Session()
    manager = ToolManager()
    await manager.load_source(MCPToolSource(session, name_prefix="remote_"))
    decision = await DefaultPolicyEngine(manager).evaluate(
        principal=_context().execution.principal,
        action="tool.execute",
        tool_name="remote_screenshot",
        arguments={},
        context=_context(),
    )
    assert decision.effect == "require_approval"
    assert decision.reason_codes == ["policy.high_risk_write"]
