"""New runtime ownership and six-stage pipeline integration tests."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, SecretStr

from examples.structured_agent.run import build_server
from examples.structured_agent.agent import RESEARCH_WRITER_AGENT
from configs import SCENARIO_CONFIGS
from examples.structured_agent.model_adapter import ScriptedModelAdapter
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.api.errors import IdempotencyConflictError, PolicyDeniedError
from usagi_agent.kernel import AuthContext
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.action import ToolAction
from usagi_agent.types.model import ModelRequest, ModelResponse
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


class _AlwaysToolModel:
    adapter_ref = "adapter.always_tool"

    async def generate(self, request: ModelRequest, ctx) -> ModelResponse:
        return ModelResponse(
            content_ref=ArtifactRef(artifact_id="placeholder", content_type="text/plain"),
            tool_calls=[ToolAction(
                tool_name="web_search",
                tool_call_id=f"tc_{ctx.execution.control_id}",
                arguments={"query": "agents"},
            )],
            finish_reason="tool_use",
        )

    async def health(self):
        return "healthy"


class _CountingSearchTool(SearchToolAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return await super().execute(arguments, context)


def _request(key: str, query: str = "frameworks") -> RunStartRequest:
    return RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key=key,
        input=_Request(query=query),
        options=RunOptions(),
    )


@pytest.mark.asyncio
async def test_two_pass_run_completes_with_tool_then_final():
    server = build_server()
    handle = await server.start_agent(_request("k1"))
    outcome = await server.get_run(handle.run_id)
    assert outcome.kind == "completed"
    assert outcome.result_ref.artifact_id == "final_output_content"
    await server.shutdown()


@pytest.mark.asyncio
async def test_idempotency_replay_and_conflict():
    server = build_server()
    h1 = await server.start_agent(_request("k2"))
    h2 = await server.start_agent(_request("k2"))
    assert h1.run_id == h2.run_id
    await server.start_agent(_request("k3", "a"))
    with pytest.raises(IdempotencyConflictError):
        await server.start_agent(_request("k3", "b"))
    await server.shutdown()


@pytest.mark.asyncio
async def test_concurrent_runs_have_distinct_contexts():
    model = ScriptedModelAdapter()
    server = build_server(model)
    first_auth = AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="user-a"),
        authorization_scope=("run.execute", "tool.execute"),
    )
    second_auth = AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="user-b"),
        authorization_scope=("run.execute",),
    )
    first, second = await asyncio.gather(
        server.start_agent(_request("concurrent-1", "a"), auth=first_auth),
        server.start_agent(_request("concurrent-2", "b"), auth=second_auth),
    )
    assert first.run_id != second.run_id
    outcomes = await asyncio.gather(server.get_run(first.run_id), server.get_run(second.run_id))
    assert [item.kind for item in outcomes] == ["completed", "completed"]
    assert model.seen_contexts[first.run_id].execution.principal == first_auth.principal
    assert model.seen_contexts[second.run_id].execution.principal == second_auth.principal
    assert model.seen_contexts[first.run_id] is not model.seen_contexts[second.run_id]
    await server.shutdown()


@pytest.mark.asyncio
async def test_run_access_checks_owner_and_scope():
    server = build_server()
    owner = PrincipalRef(principal_kind="user", principal_opaque_id="owner")
    started = await server.start_agent(
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
async def test_require_approval_suspends_and_approved_resume_completes():
    server = build_server()
    server.runtime.policy_engine = _ApprovalPolicy()
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="approver")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )
    handle = await server.start_agent(_request("approval-1"), auth=auth)
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
async def test_rejected_approval_fails_without_executing_tool():
    tool = _CountingSearchTool()
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
    )
    runtime.policy_engine = _ApprovalPolicy()
    runtime.tool_manager.register(tool)
    runtime.agent_manager.register(RESEARCH_WRITER_AGENT)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    server = Server(runtime)
    principal = PrincipalRef(principal_kind="user", principal_opaque_id="rejector")
    auth = AuthContext(
        principal=principal,
        authorization_scope=("run.execute", "run.read", "run.resume"),
    )
    handle = await server.start_agent(_request("approval-reject"), auth=auth)
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
    assert resumed.outcome.kind == "failed"
    assert tool.calls == 0
    await server.shutdown()


@pytest.mark.asyncio
async def test_tool_call_limit_stops_before_an_extra_execution():
    tool = _CountingSearchTool()
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=_AlwaysToolModel()
    )
    runtime.tool_manager.register(tool)
    runtime.agent_manager.register(RESEARCH_WRITER_AGENT)
    scenario = SCENARIO_CONFIGS[0]
    limited = scenario.model_copy(update={
        "pipeline": scenario.pipeline.model_copy(update={"max_tool_calls": 1})
    })
    ScenarioPipelineInitializer.init(runtime, (limited,))
    server = Server(runtime)
    handle = await server.start_agent(_request("tool-limit"))
    assert handle.outcome.kind == "failed"
    assert tool.calls == 1
    await server.shutdown()
