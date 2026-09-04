"""ToolRuntime execution semantics: budgets, errors, records, replay."""
from __future__ import annotations

import asyncio

import pytest

from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.persistence.inmemory.stores import InMemoryToolExecutionStore
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.tools import ToolAdapter, ToolManager, ToolSpec
from usagi_agent.types.refs import ArtifactOwner, PrincipalRef


def _context(*scopes: str, control_id: str = "run_test") -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="default",
            principal=PrincipalRef(principal_kind="user", principal_opaque_id="tester"),
            authorization_scope=tuple(scopes),
            control_kind="run",
            control_id=control_id,
            fencing_token=1,
        )
    )


class _ProbeTool(ToolAdapter):
    def __init__(self, *, spec: ToolSpec, behavior) -> None:
        self._spec = spec
        self._behavior = behavior
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:  # noqa: A003 - ToolAdapter contract
        return self._spec

    async def execute(self, arguments, context):
        self.calls += 1
        return await self._behavior(arguments)


def _manager(store: InMemoryToolExecutionStore | None = None):
    artifacts = InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )
    return ToolManager(
        execution_store=store,
        artifact_manager=artifacts,
    )


async def _ok(arguments):
    await asyncio.sleep(0)
    return {"echo": arguments.get("value")}


@pytest.mark.asyncio
async def test_success_returns_output_and_fills_latency():
    manager = _manager()
    manager.register(
        _ProbeTool(
            spec=ToolSpec(
                name="echo",
                description="echo",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            ),
            behavior=_ok,
        )
    )
    observation = await manager.execute(
        name="echo",
        arguments={"value": "hi"},
        context=_context("tool.execute"),
        tool_call_id="call-1",
    )
    assert observation.status == "success"
    assert observation.output == {"echo": "hi"}
    assert observation.error_code is None
    assert observation.latency_ms >= 0


@pytest.mark.asyncio
async def test_invalid_arguments_fail_with_reason_without_execution():
    manager = _manager()
    tool = _ProbeTool(
        spec=ToolSpec(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        behavior=_ok,
    )
    manager.register(tool)
    observation = await manager.execute(
        name="echo",
        arguments={"nope": 1},
        context=_context(),
        tool_call_id="call-2",
    )
    assert observation.status == "failed"
    assert observation.error_code == "tool.invalid_arguments"
    # Required-field violations are reported before unknown fields.
    assert "missing required argument: value" in (observation.error_message or "")

    unexpected = await manager.execute(
        name="echo",
        arguments={"value": "x", "nope": 1},
        context=_context(),
        tool_call_id="call-2b",
    )
    assert unexpected.status == "failed"
    assert "unexpected argument: nope" in (unexpected.error_message or "")
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_missing_scopes_denies_with_reason():
    manager = _manager()
    spec = ToolSpec(name="scoped", description="d", required_scopes=("tool.execute",))
    tool = _ProbeTool(spec=spec, behavior=_ok)
    manager.register(tool)
    observation = await manager.execute(
        name="scoped", arguments={}, context=_context("run.execute"), tool_call_id="c"
    )
    assert observation.status == "denied"
    assert observation.error_code == "tool.denied"
    assert "tool.execute" in (observation.error_message or "")
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_read_timeout_fails_with_reason():
    manager = _manager()
    async def slow(arguments):
        await asyncio.sleep(0.2)
        return {}
    manager.register(
        _ProbeTool(
            spec=ToolSpec(name="slow", description="d", timeout_seconds=0.01),
            behavior=slow,
        )
    )
    observation = await manager.execute(
        name="slow", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "failed"
    assert observation.error_code == "tool.timeout"
    assert "timed out" in (observation.error_message or "")


@pytest.mark.asyncio
async def test_write_timeout_is_unknown_never_assumed_failed():
    manager = _manager()
    async def slow(arguments):
        await asyncio.sleep(0.2)
        return {}
    manager.register(
        _ProbeTool(
            spec=ToolSpec(
                name="publish",
                description="d",
                risk="write",
                write_safety="at_most_once_manual",
                timeout_seconds=0.01,
            ),
            behavior=slow,
        )
    )
    observation = await manager.execute(
        name="publish", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "unknown"
    assert observation.error_code == "tool.unknown"
    assert "no automatic retry" in (observation.error_message or "")


@pytest.mark.asyncio
async def test_read_retries_transient_failure_then_succeeds():
    manager = _manager()
    attempts = 0

    async def flaky(arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("transient")
        return {"ok": True}

    manager.register(
        _ProbeTool(
            spec=ToolSpec(
                name="flaky", description="d", max_retries=1, retry_backoff_seconds=0.0
            ),
            behavior=flaky,
        )
    )
    observation = await manager.execute(
        name="flaky", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "success"
    assert attempts == 2


@pytest.mark.asyncio
async def test_adapter_exception_reports_type_name_only():
    manager = _manager()
    async def boom(arguments):
        raise RuntimeError("secret http://key@internal")
    manager.register(
        _ProbeTool(spec=ToolSpec(name="boom", description="d"), behavior=boom)
    )
    observation = await manager.execute(
        name="boom", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "failed"
    assert observation.error_code == "tool.execution_failed"
    assert observation.error_message == "RuntimeError"
    assert "secret" not in (observation.error_message or "")


@pytest.mark.asyncio
async def test_oversized_output_is_truncated():
    manager = _manager()
    async def big(arguments):
        return {"blob": "x" * 10_000}

    manager.register(
        _ProbeTool(
            spec=ToolSpec(name="big", description="d", max_output_bytes=1_000),
            behavior=big,
        )
    )
    observation = await manager.execute(
        name="big", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "success"
    assert observation.output is not None
    assert observation.output.get("_truncated") is True
    assert observation.output.get("_original_bytes", 0) > 1_000


@pytest.mark.asyncio
async def test_execution_record_settles_and_replay_skips_execution():
    store = InMemoryToolExecutionStore()
    manager = _manager(store)
    tool = _ProbeTool(spec=ToolSpec(name="echo", description="d"), behavior=_ok)
    manager.register(tool)
    ctx = _context(control_id="run_replay")

    first = await manager.execute(
        name="echo", arguments={"a": 1}, context=ctx, tool_call_id="call-x"
    )
    assert first.status == "success"
    record = await store.get("run_replay:call-x")
    assert record is not None and record.execution_status == "settled_success"
    assert record.idempotency_key

    # Stamp the persisted observation like the pipeline rule does, then replay:
    # the settled record must be returned without a second execution.
    payload = first.model_dump_json().encode()

    async def chunks():
        yield payload

    ref = await manager._artifact_manager.put(  # noqa: SLF001
        operation_id=f"tool:run_replay:call-x",
        owner=ArtifactOwner(tenant_id="default", erasure_scope_id="run_replay"),
        lineage=[],
        payload=chunks(),
        purpose="run_execution",
    )
    await manager.attach_observation("run_replay:call-x", ref.artifact_id)

    second = await manager.execute(
        name="echo", arguments={"a": 1}, context=ctx, tool_call_id="call-x"
    )
    assert tool.calls == 1
    assert second.status == "success"
    assert second.output == first.output


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_failed_observation():
    manager = _manager()
    observation = await manager.execute(
        name="ghost", arguments={}, context=_context(), tool_call_id="c"
    )
    assert observation.status == "failed"
    assert observation.error_code == "tool.execution_failed"
    assert "unknown tool" in (observation.error_message or "")


@pytest.mark.asyncio
async def test_spec_validation_rejects_inconsistent_write_safety():
    manager = _manager()
    with pytest.raises(ValueError, match="write_safety"):
        manager.register(
            _ProbeTool(
                spec=ToolSpec(
                    name="bad", description="d", risk="write", write_safety=None
                ),
                behavior=_ok,
            )
        )
    with pytest.raises(ValueError, match="write_safety"):
        manager.register(
            _ProbeTool(
                spec=ToolSpec(
                    name="bad2", description="d", risk="read", write_safety="reconcile"
                ),
                behavior=_ok,
            )
        )
