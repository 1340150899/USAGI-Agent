"""New runtime ownership and six-stage pipeline integration tests."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, SecretStr

from examples.structured_agent.run import build_server
from examples.structured_agent.agent import create_research_writer_agent
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.agents.model_adapters import ScriptedModelAdapter
from usagi_agent.api.errors import IdempotencyConflictError, PolicyDeniedError
from usagi_agent.kernel import AuthContext
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.processors.context_build import ContextBuildProcessor
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.model import ModelResponse, ModelToolCall, ModelUsage
from usagi_agent.types.policy import PolicyDecision
from usagi_agent.types.refs import ArtifactRef, PrincipalRef
from usagi_agent.types.run import (
    ApprovalResume,
    CancellationReasonCode,
    RunOptions,
    RunStartRequest,
)


class _Request(BaseModel):
    query: str


class _ApprovalPolicy:
    async def evaluate(self, **kwargs) -> PolicyDecision:
        return PolicyDecision(effect="require_approval", reason_codes=["test.approval"])


class _CountingSearchTool(SearchToolAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return await super().execute(arguments, context)


class _CapturingScriptedModel(ScriptedModelAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    async def generate(self, request, ctx):
        self.requests.append(request)
        return await super().generate(request, ctx)


class _TwoToolCapturingModel(_CapturingScriptedModel):
    async def generate(self, request, ctx):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="web_search",
                        tool_call_id="search-first",
                        raw_arguments=json.dumps({"query": "first"}),
                    ),
                    ModelToolCall(
                        tool_name="web_search",
                        tool_call_id="search-second",
                        raw_arguments=json.dumps({"query": "second"}),
                    ),
                ],
                finish_reason="tool_calls",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        return ModelResponse(
            content="The requested tool use was cancelled.",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )


def _request(key: str, query: str = "frameworks") -> RunStartRequest:
    return RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key=key,
        input=_Request(query=query),
        options=RunOptions(),
    )


@pytest.mark.asyncio
async def test_two_pass_run_completes_with_tool_then_final(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    handle = await server.create_session(_request("k1"))
    outcome = await server.get_run(handle.run_id)
    assert outcome.kind == "completed"
    assert outcome.result_ref.artifact_id
    await server.shutdown()


@pytest.mark.asyncio
async def test_force_compaction_option_reaches_pipeline_and_is_consumed(monkeypatch, tmp_path):
    server = build_server(tmp_path / "runtime.db")
    observed_modes: list[str | None] = []
    original = ContextBuildProcessor._compress_once

    async def capture_mode(self, state, context):
        observed_modes.append(state.get("context_compaction_mode"))
        return await original(self, state, context)

    monkeypatch.setattr(ContextBuildProcessor, "_compress_once", capture_mode)
    request = _request("force-compaction-option")
    request.options.context_compaction = "force"

    handle = await server.create_session(request)
    outcome = await server.get_run(handle.run_id)

    assert outcome.kind == "completed"
    assert observed_modes[0] == "force"
    assert all(mode == "auto" for mode in observed_modes[1:])
    await server.shutdown()


@pytest.mark.asyncio
async def test_idempotency_replay_and_conflict(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    h1 = await server.create_session(_request("k2"))
    h2 = await server.create_session(_request("k2"))
    assert h1.run_id == h2.run_id
    assert h1.session_id == h2.session_id
    sessions = await server.runtime.session_manager.get_for_uid("usagi-runtime")
    assert sessions is not None and sessions.id == h1.session_id
    await server.create_session(_request("k3", "a"))
    with pytest.raises(IdempotencyConflictError):
        await server.create_session(_request("k3", "b"))
    await server.shutdown()


@pytest.mark.asyncio
async def test_concurrent_idempotency_replay_uses_one_run_and_session(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    first, second = await asyncio.gather(
        server.create_session(_request("same-concurrent-key")),
        server.create_session(_request("same-concurrent-key")),
    )

    assert first.run_id == second.run_id
    assert first.session_id == second.session_id
    assert (await server.get_run(first.run_id)).kind == "completed"
    await server.shutdown()


@pytest.mark.asyncio
async def test_concurrent_runs_have_distinct_contexts(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    first_auth = AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="user-a"),
        authorization_scope=("run.execute", "tool.execute"),
    )
    second_auth = AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="user-b"),
        authorization_scope=("run.execute",),
    )
    first, second = await asyncio.gather(
        server.create_session(_request("concurrent-1", "a"), auth=first_auth),
        server.create_session(_request("concurrent-2", "b"), auth=second_auth),
    )
    assert first.run_id != second.run_id
    outcomes = await asyncio.gather(server.get_run(first.run_id), server.get_run(second.run_id))
    assert [item.kind for item in outcomes] == ["completed", "completed"]
    await server.shutdown()


@pytest.mark.asyncio
async def test_run_access_checks_owner_and_scope(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    owner = PrincipalRef(principal_kind="user", principal_opaque_id="owner")
    started = await server.create_session(
        _request("auth-owner"),
        auth=AuthContext(principal=owner, authorization_scope=("run.execute",)),
    )
    with pytest.raises(PolicyDeniedError):
        await server.get_run(
            started.run_id,
            auth=AuthContext(principal=owner, authorization_scope=("run.execute",)),
        )
    with pytest.raises(PolicyDeniedError):
        await server.cancel(
            started.run_id,
            reason_code=CancellationReasonCode.USER_REQUEST,
            auth=AuthContext(principal=owner, authorization_scope=("run.read",)),
        )
    with pytest.raises(PolicyDeniedError):
        await server.get_run(
            started.run_id,
            auth=AuthContext(
                principal=PrincipalRef(principal_kind="user", principal_opaque_id="other"),
                authorization_scope=("run.read",),
            ),
        )
    outcome = await server.get_run(
        started.run_id,
        auth=AuthContext(principal=owner, authorization_scope=("run.read",)),
    )
    assert outcome.kind == "completed"
    await server.shutdown()


@pytest.mark.asyncio
async def test_require_approval_suspends_and_approved_resume_completes(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    server.runtime.tool_manager.resolve("web_search").spec = SearchToolAdapter.spec.model_copy(update={"requires_approval": True})
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="approver")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )
    handle = await server.create_session(_request("approval-1"), auth=auth)
    assert handle.outcome.kind == "suspended"
    descriptor = handle.outcome.interrupts[0]
    token = await server.issue_resume_token(
        handle.run_id, descriptor.interrupt_id, descriptor.checkpoint_id, auth=auth
    )
    approval = await server.runtime.persistence.approval_store.get(descriptor.interrupt_id)
    assert approval is not None and approval.status == "pending", descriptor.model_dump()
    with pytest.raises(PolicyDeniedError):
        await server.resume(
            handle.run_id,
            ApprovalResume(
                interrupt_id=descriptor.interrupt_id,
                expected_checkpoint_id=descriptor.checkpoint_id,
                resume_token=SecretStr("invalid-token"),
                approval_id=approval.approval_id,
                expected_approval_version=approval.version,
                approval_scope=approval.approval_scope,
                action_hash=approval.action_hash,
                decision="approve",
            ),
            auth=auth,
        )
    resumed = await server.resume(
        handle.run_id,
        ApprovalResume(
            interrupt_id=descriptor.interrupt_id,
            expected_checkpoint_id=descriptor.checkpoint_id,
            resume_token=token.resume_token,
            approval_id=approval.approval_id,
            expected_approval_version=approval.version,
            approval_scope=approval.approval_scope,
            action_hash=approval.action_hash,
            decision="approve",
        ),
        auth=auth,
    )
    assert resumed.outcome.kind == "completed"
    decided = await server.runtime.persistence.approval_store.get(approval.approval_id)
    assert decided is not None and decided.status == "approved"
    await server.shutdown()


@pytest.mark.asyncio
async def test_rejected_approval_returns_denied_observation_to_model(tmp_path):
    tool = _CountingSearchTool()
    model = _CapturingScriptedModel()
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    runtime.agent_manager.set_model_adapter(model)
    runtime.policy_engine = _ApprovalPolicy()
    runtime.tool_manager.register(tool)
    server = Server(runtime)
    create_research_writer_agent(server)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="rejector")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )
    handle = await server.create_session(_request("approval-reject"), auth=auth)
    assert handle.outcome.kind == "suspended"
    descriptor = handle.outcome.interrupts[0]
    approval = await runtime.persistence.approval_store.get(descriptor.interrupt_id)
    assert approval is not None
    token = await server.issue_resume_token(
        handle.run_id, descriptor.interrupt_id, descriptor.checkpoint_id, auth=auth
    )
    resumed = await server.resume(
        handle.run_id,
        ApprovalResume(
            interrupt_id=descriptor.interrupt_id,
            expected_checkpoint_id=descriptor.checkpoint_id,
            resume_token=token.resume_token,
            approval_id=approval.approval_id,
            expected_approval_version=approval.version,
            approval_scope=approval.approval_scope,
            action_hash=approval.action_hash,
            decision="reject",
        ),
        auth=auth,
    )
    assert resumed.outcome.kind == "completed"
    assert tool.calls == 0
    tool_messages = [
        message
        for request in model.requests
        for message in request.messages
        if message.get("role") == "tool"
    ]
    assert tool_messages
    rejected = json.loads(str(tool_messages[-1]["content"]))
    assert rejected == {
        "status": "denied",
        "error_code": "tool.approval_rejected",
        "reason": "The user rejected this tool execution request.",
    }
    await server.shutdown()


@pytest.mark.asyncio
async def test_pending_approval_repairs_legacy_empty_completed_run(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    server.runtime.tool_manager.resolve("web_search").spec = (
        SearchToolAdapter.spec.model_copy(update={"requires_approval": True})
    )
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="approver")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )
    started = await server.create_session(_request("legacy-empty-result"), auth=auth)
    assert started.outcome.kind == "suspended"

    store = server.runtime.persistence.run_control_store
    state = await store.get(started.run_id)
    assert state is not None
    corrupt = state.model_copy(
        update={
            "run_status": "completed",
            "final_result_ref": ArtifactRef(
                artifact_id="", content_type="application/octet-stream"
            ),
        }
    )
    await store.cas_update(started.run_id, state.version, corrupt)

    reminder = await server.continue_session(
        started.session_id,
        _request("legacy-empty-result-reminder", "hello"),
        auth=auth,
    )

    assert reminder.outcome.kind == "suspended"
    repaired = await store.get(started.run_id)
    assert repaired is not None and repaired.run_status == "suspended"
    await server.shutdown()


@pytest.mark.asyncio
async def test_rejecting_multiple_tools_repeats_approval_then_returns_reply(tmp_path):
    tool = _CountingSearchTool()
    model = _TwoToolCapturingModel()
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    runtime.agent_manager.set_model_adapter(model)
    runtime.policy_engine = _ApprovalPolicy()
    runtime.tool_manager.register(tool)
    server = Server(runtime)
    create_research_writer_agent(server)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="multi-rejector")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )

    started = await server.create_session(_request("approval-multi-reject"), auth=auth)
    assert started.outcome.kind == "suspended"
    first_pending = await runtime.persistence.approval_store.list_pending_by_session(
        started.session_id
    )
    assert len(first_pending) == 1

    next_approval = await server.continue_session(
        started.session_id,
        _request("approval-multi-reject-answer", "No"),
        auth=auth,
    )

    assert next_approval.outcome.kind == "suspended"
    second_pending = await runtime.persistence.approval_store.list_pending_by_session(
        started.session_id
    )
    assert len(second_pending) == 1
    assert second_pending[0].approval_id != first_pending[0].approval_id
    assert "web_search" in next_approval.message
    assert tool.calls == 0

    resumed = await server.continue_session(
        started.session_id,
        _request("approval-multi-reject-second-answer", "No"),
        auth=auth,
    )

    assert resumed.outcome.kind == "completed"
    assert resumed.outcome.result_ref.artifact_id
    assert resumed.message == "The requested tool use was cancelled."
    assert not await runtime.persistence.approval_store.list_pending_by_session(
        started.session_id
    )
    tool_messages = [
        message
        for request in model.requests
        for message in request.messages
        if message.get("role") == "tool"
    ]
    assert len(tool_messages) >= 2
    for message in tool_messages[-2:]:
        rejected = json.loads(str(message["content"]))
        assert rejected["status"] == "denied"
        assert rejected["error_code"] == "tool.approval_rejected"
    await server.shutdown()


@pytest.mark.asyncio
async def test_tool_call_limit_stops_before_an_extra_execution(tmp_path):
    tool = _CountingSearchTool()
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    runtime.tool_manager.register(tool)
    server = Server(runtime)
    create_research_writer_agent(server)
    scenario = SCENARIO_CONFIGS[0]
    limited = scenario.model_copy(update={
        "pipeline": scenario.pipeline.model_copy(update={"max_tool_calls": 0})
    })
    ScenarioPipelineInitializer.init(runtime, (limited,))
    handle = await server.create_session(_request("tool-limit"))
    assert handle.outcome.kind == "failed"
    assert tool.calls == 0
    await server.shutdown()
