"""MCP tools adapted into USAGI's governed ToolManager contracts.

The source accepts an initialized MCP ClientSession-like object.  Connection
lifecycle and credentials remain service-bootstrap concerns; tool execution,
risk policy and artifacts remain governed by USAGI.
"""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.ports.context import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.content import ContentPart, ImageContentPart
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef
from usagi_agent.types.tool import ToolAdapterResult, ToolSpec


class MCPClientSession(Protocol):
    async def list_tools(self) -> object: ...

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> object: ...


def _value(value: object, *names: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


class MCPToolAdapter(ToolAdapter):
    def __init__(
        self,
        *,
        session: MCPClientSession,
        remote_name: str,
        spec: ToolSpec,
        artifact_manager: ArtifactManager | None = None,
    ) -> None:
        self._session = session
        self._remote_name = remote_name
        self.spec = spec
        self._artifact_manager = artifact_manager

    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> ToolAdapterResult:
        result = await self._session.call_tool(self._remote_name, arguments)
        if bool(_value(result, "isError", "is_error", default=False)):
            raise RuntimeError("MCP tool returned an error result")

        structured = _value(result, "structuredContent", "structured_content")
        output: dict[str, object] = (
            dict(structured) if isinstance(structured, Mapping) else {}
        )
        rendered_blocks: list[dict[str, object]] = []
        image_parts: list[ContentPart] = []
        artifact_refs: list[ArtifactRef] = []

        blocks = _value(result, "content", default=[])
        for block in blocks if isinstance(blocks, list) else []:
            block_type = str(_value(block, "type", default=""))
            if block_type == "text":
                rendered_blocks.append(
                    {"type": "text", "text": str(_value(block, "text", default=""))}
                )
                continue
            if block_type == "image":
                data = _value(block, "data")
                media_type = str(
                    _value(block, "mimeType", "mime_type", default="image/png")
                )
                if isinstance(data, str):
                    ref = await self._store_binary(
                        base64.b64decode(data), media_type, context
                    )
                    artifact_refs.append(ref)
                    image_parts.append(
                        ImageContentPart(media_type=media_type, artifact_ref=ref)
                    )
                    rendered_blocks.append(
                        {"type": "image", "artifact_ref": ref.model_dump(mode="json")}
                    )
                continue
            rendered_blocks.append(
                {"type": block_type or "unknown", "value": str(block)}
            )

        if rendered_blocks:
            output.setdefault("content", rendered_blocks)
        return ToolAdapterResult(
            output=output,
            content_parts=image_parts,
            artifact_refs=artifact_refs,
        )

    async def _store_binary(
        self, payload: bytes, media_type: str, context: ToolContext
    ) -> ArtifactRef:
        if self._artifact_manager is None:
            raise RuntimeError("MCP binary output requires an ArtifactManager")

        async def chunks():
            yield payload

        digest = hashlib.sha256(payload).hexdigest()[:24]
        ref = await self._artifact_manager.put(
            operation_id=(
                f"mcp:{context.execution.control_id}:{self._remote_name}:{digest}"
            ),
            owner=ArtifactOwner(
                tenant_id=context.execution.tenant_id,
                erasure_scope_id=context.execution.control_id,
            ),
            lineage=[],
            payload=chunks(),
            purpose="run_execution",
        )
        return ref.model_copy(update={"content_type": media_type})


class MCPToolSource:
    """Discover MCP tools and expose them as governed USAGI adapters."""

    def __init__(
        self,
        session: MCPClientSession,
        *,
        artifact_manager: ArtifactManager | None = None,
        name_prefix: str = "",
        spec_overrides: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        self._session = session
        self._artifact_manager = artifact_manager
        self._name_prefix = name_prefix
        self._spec_overrides = spec_overrides or {}

    async def load(self) -> tuple[MCPToolAdapter, ...]:
        response = await self._session.list_tools()
        raw_tools = _value(response, "tools", default=response)
        tools: list[MCPToolAdapter] = []
        for raw in raw_tools if isinstance(raw_tools, (list, tuple)) else []:
            remote_name = str(_value(raw, "name", default=""))
            if not remote_name:
                continue
            local_name = f"{self._name_prefix}{remote_name}"
            parameters = _value(raw, "inputSchema", "input_schema", default={})
            base: dict[str, Any] = {
                "name": local_name,
                "description": str(_value(raw, "description", default="")),
                "parameters": dict(parameters) if isinstance(parameters, Mapping) else {},
                "adapter_kind": "mcp",
            }
            base.update(self._spec_overrides.get(remote_name, {}))
            base["name"] = local_name
            base["adapter_kind"] = "mcp"
            spec = ToolSpec.model_validate(base)
            tools.append(
                MCPToolAdapter(
                    session=self._session,
                    remote_name=remote_name,
                    spec=spec,
                    artifact_manager=self._artifact_manager,
                )
            )
        return tuple(tools)
