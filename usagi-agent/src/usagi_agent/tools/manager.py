"""Service-lifetime tool ownership and execution (ToolRuntime v1, design §21.5).

ToolManager is the registry AND the v1 ToolRuntime implementation: one
``execute`` call runs the standardized pipeline

    resolve -> scope check -> execution record (reserve/replay/settle)
    -> argument validation -> concurrency gate + timeout (+ read retry)
    -> output normalization -> ToolObservation

Every failure becomes a structured observation with a safe reason so the
model can see *why* a call failed and adapt on the next pass. Only write-class
timeouts/crashes produce ``status="unknown"`` — those must never be assumed
not-executed (§21.7).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import BaseModel

from usagi_agent.api.errors import CASMismatch, DuplicateToolError, UnknownToolError
from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.persistence.ports.execution import (
    ToolExecutionRecord,
    ToolExecutionStore,
)
from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.tools.validation import validate_arguments
from usagi_agent.ports.tool import ToolSource
from usagi_agent.types.action import ToolObservation
from usagi_agent.types.content import ContentPart
from usagi_agent.types.refs import ArtifactRef
from usagi_agent.types.tool import ToolAdapterResult, ToolSpec

if TYPE_CHECKING:
    from usagi_agent.tools.mcp import MCPServerConfig

_SETTLED = ("settled_success", "settled_failure")


class ToolManager:
    def __init__(
        self,
        *,
        execution_store: ToolExecutionStore | None = None,
        artifact_manager: ArtifactManager | None = None,
    ) -> None:
        self._tools: dict[str, ToolAdapter] = {}
        self._execution_store = execution_store
        self._artifact_manager = artifact_manager
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._sources: list[ToolSource] = []

    # ------------------------------------------------------------------ registry

    def register(self, adapter: ToolAdapter) -> None:
        spec = adapter.spec
        if spec.name in self._tools:
            raise DuplicateToolError(spec.name)
        self._validate_spec(spec)
        self._tools[spec.name] = adapter
        if spec.max_concurrency > 0:
            self._semaphores[spec.name] = asyncio.Semaphore(spec.max_concurrency)

    @staticmethod
    def _validate_spec(spec: ToolSpec) -> None:
        """Bootstrap-time capability consistency (§21.3)."""
        if spec.risk == "read":
            if spec.write_safety is not None:
                raise ValueError(
                    f"tool {spec.name!r}: read tools must not declare write_safety"
                )
            return
        if spec.write_safety is None:
            raise ValueError(
                f"tool {spec.name!r}: write tools must declare a write_safety mode"
            )

    def register_many(self, adapters: Iterable[ToolAdapter]) -> None:
        for adapter in adapters:
            self.register(adapter)

    async def register_mcp(
        self, config: MCPServerConfig
    ) -> tuple[ToolAdapter, ...]:
        """Connect and register one MCP server from its public configuration.

        This is the application-facing MCP entry point. The manager constructs
        the source with its own ArtifactManager, discovers the remote tools, and
        owns the connection until ``shutdown()``.
        """

        from usagi_agent.tools.mcp import MCPServerSource

        source = MCPServerSource(
            config,
            artifact_manager=self._artifact_manager,
        )
        return await self.load_source(source)

    async def load_source(self, source: ToolSource) -> tuple[ToolAdapter, ...]:
        """Discover and register every adapter from a dynamic tool source."""
        try:
            loaded = await source.load()
            adapters = tuple(loaded)
            pending_names: set[str] = set()
            for adapter in adapters:
                if not isinstance(adapter, ToolAdapter):
                    raise TypeError("tool source returned a non-ToolAdapter value")
                if adapter.spec.name in self._tools or adapter.spec.name in pending_names:
                    raise DuplicateToolError(adapter.spec.name)
                pending_names.add(adapter.spec.name)
                self._validate_spec(adapter.spec)
            self.register_many(adapters)
            self._sources.append(source)
            return adapters
        except BaseException:
            close = getattr(source, "shutdown", None)
            if close is not None:
                with suppress(Exception):
                    await close()
            raise

    def resolve(self, name: str) -> ToolAdapter:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(name) from exc

    def get_spec(self, name: str) -> ToolSpec:
        return self.resolve(name).spec

    def all_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(adapter.spec for adapter in self._tools.values())

    def get_specs(self, names: Iterable[str]) -> tuple[ToolSpec, ...]:
        return tuple(self.get_spec(name) for name in names)

    # ----------------------------------------------------------------- execution

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        tool_call_id: str = "",
        operation_id: str | None = None,
    ) -> ToolObservation:
        started = time.monotonic()
        try:
            adapter = self.resolve(name)
        except UnknownToolError:
            return self._observation(
                name, tool_call_id, started, "failed",
                error_code="tool.execution_failed",
                error_message=f"unknown tool: {name}",
            )
        spec = adapter.spec

        missing = [
            scope
            for scope in spec.required_scopes
            if scope not in context.execution.authorization_scope
        ]
        if missing:
            return self._observation(
                name, tool_call_id, started, "denied",
                error_code="tool.denied",
                error_message=f"missing required scopes: {', '.join(missing)}",
            )

        invalid = validate_arguments(spec.parameters, arguments)
        if invalid is not None:
            return self._observation(
                name, tool_call_id, started, "failed",
                error_code="tool.invalid_arguments",
                error_message=invalid,
            )

        execution_id = operation_id or self._execution_id(name, context, tool_call_id)
        if self._execution_store is not None:
            replayed = await self._replay_settled(execution_id)
            if replayed is not None:
                return replayed
            reserved = await self._reserve(execution_id, name, spec, context)
            if reserved is None:
                # The record exists in a non-settled state (concurrent execution
                # or an unrecovered crash); the side effect's absence cannot be
                # assumed.
                return self._observation(
                    name, tool_call_id, started, "unknown",
                    error_code="tool.unknown",
                    error_message="execution record is already reserved or executing",
                )
            try:
                await self._execution_store.cas_execution_status(
                    execution_id, expected="reserved", new="executing"
                )
            except CASMismatch:
                return self._observation(
                    name, tool_call_id, started, "unknown",
                    error_code="tool.unknown",
                    error_message="execution record state changed concurrently",
                )

        outcome = await self._execute_with_budget(adapter, spec, arguments, context)
        result = outcome[1]
        observation = self._observation(
            name,
            tool_call_id,
            started,
            outcome[0],
            output=result.output if result is not None else None,
            content_parts=result.content_parts if result is not None else None,
            artifact_refs=result.artifact_refs if result is not None else None,
            error_code=outcome[2],
            error_message=outcome[3],
        )
        await self._settle(execution_id, observation)
        return observation

    async def _execute_with_budget(
        self,
        adapter: ToolAdapter,
        spec: ToolSpec,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> tuple[str, ToolAdapterResult | None, str | None, str | None]:
        """Run the adapter under concurrency/timeout with read-class retry."""

        attempts = spec.max_retries + 1 if spec.risk == "read" else 1
        semaphore = self._semaphores.get(spec.name)

        async def _run_once() -> ToolAdapterResult:
            raw = await adapter.execute(arguments, context)
            if isinstance(raw, ToolAdapterResult):
                return raw
            if isinstance(raw, BaseModel):
                return ToolAdapterResult(output=raw.model_dump(mode="json"))
            if isinstance(raw, dict):
                return ToolAdapterResult(output=dict(raw))
            return ToolAdapterResult(
                output={"_unserializable": type(raw).__name__}
            )

        for attempt in range(attempts):
            try:
                if semaphore is not None:
                    async with semaphore:
                        output = await asyncio.wait_for(
                            _run_once(), timeout=spec.timeout_seconds
                        )
                else:
                    output = await asyncio.wait_for(
                        _run_once(), timeout=spec.timeout_seconds
                    )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if spec.risk != "read":
                    return (
                        "unknown", None, "tool.unknown",
                        "timed out; the side effect may have been applied, "
                        "no automatic retry",
                    )
                if attempt + 1 < attempts:
                    await asyncio.sleep(spec.retry_backoff_seconds)
                    continue
                return (
                    "failed", None, "tool.timeout",
                    f"timed out after {spec.timeout_seconds:g}s",
                )
            except Exception as exc:
                transient = isinstance(exc, (TimeoutError, ConnectionError, OSError))
                if spec.risk == "read" and transient and attempt + 1 < attempts:
                    await asyncio.sleep(spec.retry_backoff_seconds)
                    continue
                return (
                    "failed", None, "tool.execution_failed", type(exc).__name__
                )
            return "success", self._truncate_result(spec, output), None, None
        return (
            "failed", None, "tool.execution_failed", "retry budget exhausted"
        )

    @staticmethod
    def _truncate(spec: ToolSpec, output: dict[str, object]) -> dict[str, object]:
        payload = json.dumps(output, ensure_ascii=False, default=str)
        if len(payload.encode()) <= spec.max_output_bytes:
            return output
        preview_budget = spec.max_output_bytes // 2
        return {
            "_truncated": True,
            "_original_bytes": len(payload.encode()),
            "preview": payload[:preview_budget],
        }

    @classmethod
    def _truncate_result(
        cls, spec: ToolSpec, result: ToolAdapterResult
    ) -> ToolAdapterResult:
        return result.model_copy(update={"output": cls._truncate(spec, result.output)})

    # --------------------------------------------------------- execution records

    @staticmethod
    def _execution_id(
        name: str, context: ToolContext, tool_call_id: str
    ) -> str:
        suffix = tool_call_id or sha256(
            f"{name}:{sorted(context.execution.authorization_scope)}".encode()
        ).hexdigest()[:12]
        return f"{context.execution.control_id}:{suffix}"

    async def _replay_settled(self, execution_id: str) -> ToolObservation | None:
        if self._execution_store is None:
            return None
        record = await self._execution_store.get(execution_id)
        if record is None or record.execution_status not in _SETTLED:
            return None
        if record.observation_ref and self._artifact_manager is not None:
            observation = await self._load_observation(record.observation_ref)
            if observation is not None:
                return observation
        return self._observation(
            record.tool_name, "", 0.0, "unknown",
            error_code="tool.unknown",
            error_message="settled execution has no replayable observation",
        )

    async def _load_observation(self, artifact_id: str) -> ToolObservation | None:
        payload = bytearray()
        stream = await self._artifact_manager.get(  # type: ignore[union-attr]
            ArtifactRef(artifact_id=artifact_id, content_type="application/json"),
            "run_execution",
        )
        async for chunk in stream:
            payload.extend(chunk)
        return ToolObservation.model_validate_json(bytes(payload))

    async def _reserve(
        self, execution_id: str, name: str, spec: ToolSpec, context: ToolContext
    ):
        """Reserve the execution record; None means a conflicting record exists."""
        if self._execution_store is None:
            return True
        existing = await self._execution_store.get(execution_id)
        if existing is not None:
            return None
        now = datetime.now(timezone.utc)
        await self._execution_store.reserve(
            ToolManager._record(execution_id, name, spec, context, now)
        )
        return True

    @staticmethod
    def _record(
        execution_id: str,
        name: str,
        spec: ToolSpec,
        context: ToolContext,
        now: datetime,
    ) -> ToolExecutionRecord:
        idempotency_key = sha256(
            "|".join(
                (
                    name,
                    spec.write_safety or "",
                    context.execution.tenant_id,
                    context.execution.control_id,
                    execution_id,
                )
            ).encode()
        ).hexdigest()
        return ToolExecutionRecord(
            execution_id=execution_id,
            tenant_id=context.execution.tenant_id,
            run_id=context.execution.control_id,
            tool_name=name,
            idempotency_key=idempotency_key,
            write_safety=spec.write_safety,
            created_at=now,
            updated_at=now,
        )

    async def _settle(
        self, execution_id: str, observation: ToolObservation
    ) -> None:
        if self._execution_store is None:
            return
        status = (
            "settled_success"
            if observation.status == "success"
            else "unknown" if observation.status == "unknown" else "settled_failure"
        )
        try:
            await self._execution_store.cas_execution_status(
                execution_id, expected="executing", new=status
            )
        except CASMismatch:
            # A reconciler or late settler moved the record first; first
            # settlement stays immutable (§21.7) so we only drop this update.
            pass

    async def attach_observation(
        self, execution_id: str, observation_ref: str
    ) -> None:
        """Stamp the persisted observation artifact onto the execution record.

        Called by the pipeline after the observation is stored as an artifact;
        closes the replay window between settle and persistence.
        """
        if self._execution_store is None:
            return
        record = await self._execution_store.get(execution_id)
        if record is None or record.observation_ref is not None:
            return
        await self._execution_store.reserve(
            record.model_copy(update={"observation_ref": observation_ref})
        )

    # ------------------------------------------------------------------ plumbing

    @staticmethod
    def _observation(
        name: str,
        tool_call_id: str,
        started: float,
        status: str,
        *,
        output: dict[str, object] | None = None,
        content_parts: list[ContentPart] | None = None,
        artifact_refs: list[ArtifactRef] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ToolObservation:
        return ToolObservation(
            tool_name=name,
            tool_call_id=tool_call_id,
            status=status,  # type: ignore[arg-type]
            output=output,
            content_parts=content_parts or [],
            artifact_refs=artifact_refs or [],
            error_code=error_code,
            error_message=error_message,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def health(self) -> dict[str, HealthStatus]:
        statuses: dict[str, HealthStatus] = {}
        for name, adapter in self._tools.items():
            try:
                statuses[name] = await adapter.health()
            except Exception:
                statuses[name] = "unhealthy"
        for source in self._sources:
            health = getattr(source, "health", None)
            if health is None:
                continue
            source_name = str(getattr(source, "name", type(source).__name__))
            try:
                statuses[source_name] = await health()
            except Exception:
                statuses[source_name] = "unhealthy"
        return statuses

    async def shutdown(self) -> None:
        errors: list[Exception] = []
        for adapter in reversed(tuple(self._tools.values())):
            try:
                await adapter.shutdown()
            except Exception as exc:
                errors.append(exc)
        for source in reversed(self._sources):
            close = getattr(source, "shutdown", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise ExceptionGroup("one or more tools failed to shut down", errors)
