"""MCP client connections adapted into USAGI's governed tool contracts."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.content import ContentPart, ImageContentPart
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef
from usagi_agent.types.tool import ToolAdapterResult, ToolSpec


class MCPClientSession(Protocol):
    async def list_tools(self, *, cursor: str | None = None) -> object: ...

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> object: ...


class MCPServerConfig(BaseModel):
    """Deployment configuration for one service-lifetime MCP connection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    transport: Literal["stdio", "streamable_http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: dict[str, SecretStr] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    verify_tls: bool = True
    ca_bundle: Path | None = None
    client_certificate: Path | None = None
    client_key: Path | None = None
    proxy_url: str | None = None
    http2: bool = False
    terminate_on_close: bool = True
    name_prefix: str | None = None
    enabled_tools: tuple[str, ...] | None = None
    disabled_tools: tuple[str, ...] = ()
    require_tools: bool = True
    required_scopes: tuple[str, ...] = ()
    spec_overrides: dict[str, dict[str, object]] = Field(default_factory=dict)
    startup_timeout_seconds: float = Field(default=30.0, gt=0)
    read_timeout_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerConfig":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio MCP server requires command")
            if (
                self.url is not None
                or self.headers
                or not self.verify_tls
                or self.ca_bundle is not None
                or self.client_certificate is not None
                or self.client_key is not None
                or self.proxy_url is not None
                or self.http2
                or not self.terminate_on_close
            ):
                raise ValueError("stdio MCP server does not accept HTTP fields")
        else:
            if not self.url:
                raise ValueError("streamable_http MCP server requires url")
            if self.command is not None or self.args or self.cwd is not None or self.env:
                raise ValueError(
                    "streamable_http MCP server does not accept stdio process fields"
                )
            if not self.url.startswith(("http://", "https://")):
                raise ValueError("MCP server url must use http or https")
            if self.ca_bundle is not None and not self.verify_tls:
                raise ValueError("ca_bundle cannot be used when verify_tls is false")
            if self.client_key is not None and self.client_certificate is None:
                raise ValueError("client_key requires client_certificate")
        return self

    @property
    def effective_prefix(self) -> str:
        return self.name_prefix if self.name_prefix is not None else f"{self.name}__"


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


def _plain(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_local_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    if not normalized:
        raise ValueError(f"MCP tool name {name!r} has no model-safe characters")
    if len(normalized) <= 64:
        return normalized
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{normalized[:55]}_{digest}"


class MCPConnection:
    """Own an official SDK Client context in one long-lived asyncio task."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._client: object | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._ready: asyncio.Future[None] | None = None
        self._failure: BaseException | None = None

    async def start(self) -> None:
        if self._runner_task is not None:
            return
        loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready = loop.create_future()
        self._runner_task = asyncio.create_task(
            self._run(), name=f"mcp:{self.config.name}"
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(self._ready), self.config.startup_timeout_seconds
            )
        except BaseException:
            if self._ready is not None and not self._ready.done():
                self._ready.cancel()
                assert self._runner_task is not None
                self._runner_task.cancel()
            await self.shutdown()
            raise

    async def _run(self) -> None:
        assert self._ready is not None and self._stop is not None
        try:
            from contextlib import AsyncExitStack

            from mcp import Client, StdioServerParameters

            async with AsyncExitStack() as stack:
                if self.config.transport == "stdio":
                    target: object = StdioServerParameters(
                        command=self.config.command or "",
                        args=list(self.config.args),
                        cwd=self.config.cwd,
                        env={
                            key: value.get_secret_value()
                            for key, value in self.config.env.items()
                        }
                        or None,
                    )
                else:
                    import httpx2
                    from mcp.client.streamable_http import streamable_http_client

                    verify: bool | str = (
                        str(self.config.ca_bundle)
                        if self.config.ca_bundle is not None
                        else self.config.verify_tls
                    )
                    cert: str | tuple[str, str] | None = None
                    if self.config.client_certificate is not None:
                        cert = str(self.config.client_certificate)
                        if self.config.client_key is not None:
                            cert = (cert, str(self.config.client_key))
                    http_client = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            headers={
                                key: value.get_secret_value()
                                for key, value in self.config.headers.items()
                            },
                            follow_redirects=True,
                            verify=verify,
                            cert=cert,
                            proxy=self.config.proxy_url,
                            http2=self.config.http2,
                            timeout=httpx2.Timeout(
                                self.config.read_timeout_seconds,
                                connect=self.config.startup_timeout_seconds,
                            ),
                        )
                    )
                    target = streamable_http_client(
                        self.config.url or "",
                        http_client=http_client,
                        terminate_on_close=self.config.terminate_on_close,
                    )
                client = Client(
                    target, read_timeout_seconds=self.config.read_timeout_seconds
                )
                self._client = await stack.enter_async_context(client)
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException as exc:
            self._failure = exc
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._client = None

    def _connected_client(self) -> object:
        if self._client is None:
            if self._failure is not None:
                raise ConnectionError(
                    f"MCP server {self.config.name!r} disconnected"
                ) from self._failure
            raise ConnectionError(f"MCP server {self.config.name!r} is not connected")
        return self._client

    async def list_tools(self, **kwargs: object) -> object:
        client = self._connected_client()
        return await client.list_tools(**kwargs)  # type: ignore[attr-defined]

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> object:
        client = self._connected_client()
        return await client.call_tool(name, arguments)  # type: ignore[attr-defined]

    async def health(self) -> HealthStatus:
        try:
            client = self._connected_client()
            await asyncio.wait_for(
                client.list_tools(cache_mode="bypass"), timeout=5.0  # type: ignore[attr-defined]
            )
        except Exception:
            return "unhealthy"
        return "healthy"

    async def shutdown(self) -> None:
        task = self._runner_task
        if task is None:
            return
        if self._stop is not None:
            self._stop.set()
        try:
            await task
        finally:
            self._runner_task = None


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
        plain_structured = _plain(structured)
        if isinstance(plain_structured, Mapping):
            output: dict[str, object] = dict(plain_structured)
        elif plain_structured is not None:
            output = {"structured_content": plain_structured}
        else:
            output = {}
        rendered_blocks: list[dict[str, object]] = []
        image_parts: list[ContentPart] = []
        artifact_refs: list[ArtifactRef] = []

        blocks = _value(result, "content", default=[])
        for index, block in enumerate(blocks if isinstance(blocks, list) else []):
            block_type = str(_value(block, "type", default=""))
            if block_type == "text":
                rendered_blocks.append(
                    {"type": "text", "text": str(_value(block, "text", default=""))}
                )
                continue
            if block_type in {"image", "audio"}:
                data = _value(block, "data")
                default_type = "image/png" if block_type == "image" else "audio/mpeg"
                media_type = str(
                    _value(block, "mimeType", "mime_type", default=default_type)
                )
                if isinstance(data, str):
                    payload = base64.b64decode(data, validate=True)
                    ref = await self._store_binary(
                        payload, media_type, context, suffix=str(index)
                    )
                    artifact_refs.append(ref)
                    if block_type == "image":
                        image_parts.append(
                            ImageContentPart(media_type=media_type, artifact_ref=ref)
                        )
                    rendered_blocks.append(
                        {
                            "type": block_type,
                            "media_type": media_type,
                            "artifact_ref": ref.model_dump(mode="json"),
                        }
                    )
                continue
            if block_type == "resource":
                resource = _value(block, "resource", default={})
                blob = _value(resource, "blob")
                if isinstance(blob, str):
                    media_type = str(
                        _value(
                            resource,
                            "mimeType",
                            "mime_type",
                            default="application/octet-stream",
                        )
                    )
                    payload = base64.b64decode(blob, validate=True)
                    ref = await self._store_binary(
                        payload, media_type, context, suffix=str(index)
                    )
                    artifact_refs.append(ref)
                    rendered_blocks.append(
                        {
                            "type": "resource",
                            "uri": str(_value(resource, "uri", default="")),
                            "media_type": media_type,
                            "artifact_ref": ref.model_dump(mode="json"),
                        }
                    )
                else:
                    rendered_blocks.append(
                        {"type": "resource", "value": _plain(resource)}
                    )
                continue
            rendered_blocks.append(
                {"type": block_type or "unknown", "value": _plain(block)}
            )

        if rendered_blocks:
            output.setdefault("content", rendered_blocks)
        return ToolAdapterResult(
            output=output,
            content_parts=image_parts,
            artifact_refs=artifact_refs,
        )

    async def _store_binary(
        self,
        payload: bytes,
        media_type: str,
        context: ToolContext,
        *,
        suffix: str,
    ) -> ArtifactRef:
        if self._artifact_manager is None:
            raise RuntimeError("MCP binary output requires an ArtifactManager")

        async def chunks():
            yield payload

        digest = hashlib.sha256(payload).hexdigest()[:24]
        ref = await self._artifact_manager.put(
            operation_id=(
                f"mcp:{context.execution.control_id}:{self._remote_name}:"
                f"{suffix}:{digest}"
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
    """Discover MCP tools from an initialized, externally-owned session."""

    def __init__(
        self,
        session: MCPClientSession,
        *,
        artifact_manager: ArtifactManager | None = None,
        name_prefix: str = "",
        spec_overrides: Mapping[str, Mapping[str, object]] | None = None,
        enabled_tools: tuple[str, ...] | None = None,
        disabled_tools: tuple[str, ...] = (),
        required_scopes: tuple[str, ...] = (),
    ) -> None:
        self._session = session
        self._artifact_manager = artifact_manager
        self._name_prefix = name_prefix
        self._spec_overrides = spec_overrides or {}
        self._enabled_tools = (
            frozenset(enabled_tools) if enabled_tools is not None else None
        )
        self._disabled_tools = frozenset(disabled_tools)
        self._required_scopes = required_scopes

    async def _list_all_tools(self) -> list[object]:
        response = await self._session.list_tools()
        result: list[object] = []
        seen_cursors: set[str] = set()
        while True:
            raw_tools = _value(response, "tools", default=response)
            if isinstance(raw_tools, (list, tuple)):
                result.extend(raw_tools)
            cursor = _value(response, "nextCursor", "next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                return result
            seen_cursors.add(cursor)
            response = await self._session.list_tools(cursor=cursor)

    async def load(self) -> tuple[MCPToolAdapter, ...]:
        raw_tools = await self._list_all_tools()
        tools: list[MCPToolAdapter] = []
        local_names: set[str] = set()
        for raw in raw_tools:
            remote_name = str(_value(raw, "name", default=""))
            if not remote_name:
                continue
            if self._enabled_tools is not None and remote_name not in self._enabled_tools:
                continue
            if remote_name in self._disabled_tools:
                continue
            local_name = _safe_local_name(f"{self._name_prefix}{remote_name}")
            if local_name in local_names:
                raise ValueError(
                    f"MCP tool names collide after normalization: {local_name!r}"
                )
            local_names.add(local_name)
            parameters = _value(raw, "inputSchema", "input_schema", default={})
            annotations = _value(raw, "annotations", default={})
            is_read_only = _value(
                annotations, "readOnlyHint", "read_only_hint", default=False
            ) is True
            description = _value(raw, "description", default="")
            base: dict[str, Any] = {
                "name": local_name,
                "description": "" if description is None else str(description),
                "parameters": dict(parameters) if isinstance(parameters, Mapping) else {},
                "risk": "read" if is_read_only else "high_risk_write",
                "write_safety": None if is_read_only else "at_most_once_manual",
                "required_scopes": self._required_scopes,
                "adapter_kind": "mcp",
            }
            base.update(self._spec_overrides.get(remote_name, {}))
            override = self._spec_overrides.get(remote_name, {})
            if override.get("risk") == "read" and "write_safety" not in override:
                base["write_safety"] = None
            elif override.get("risk") in {"write", "high_risk_write"} and (
                "write_safety" not in override
            ):
                base["write_safety"] = "at_most_once_manual"
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


class MCPServerSource(MCPToolSource):
    """Connect, discover and own one configured MCP server."""

    def __init__(
        self, config: MCPServerConfig, *, artifact_manager: ArtifactManager
    ) -> None:
        self.name = f"mcp:{config.name}"
        self.connection = MCPConnection(config)
        super().__init__(
            self.connection,
            artifact_manager=artifact_manager,
            name_prefix=config.effective_prefix,
            spec_overrides=config.spec_overrides,
            enabled_tools=config.enabled_tools,
            disabled_tools=config.disabled_tools,
            required_scopes=config.required_scopes,
        )

    async def load(self) -> tuple[MCPToolAdapter, ...]:
        await self.connection.start()
        try:
            tools = await super().load()
            if self.connection.config.require_tools and not tools:
                raise RuntimeError(
                    f"MCP server {self.connection.config.name!r} exposed no enabled tools"
                )
            return tools
        except BaseException:
            await self.connection.shutdown()
            raise

    async def health(self) -> HealthStatus:
        return await self.connection.health()

    async def shutdown(self) -> None:
        await self.connection.shutdown()
