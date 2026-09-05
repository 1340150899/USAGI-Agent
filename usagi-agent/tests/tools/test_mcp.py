from __future__ import annotations

import base64

import pytest

from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.tools import MCPToolSource, ToolManager
from usagi_agent.types.refs import PrincipalRef


class _Session:
    calls: list[tuple[str, dict[str, object]]]

    def __init__(self) -> None:
        self.calls = []

    async def list_tools(self):
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
