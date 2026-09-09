"""Tool-owned approvals, including direct callers and LangGraph node replay."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

from usagi_agent.persistence.inmemory.stores import InMemoryApprovalStore
from usagi_agent.tools.approval import ToolApprovalRequired
from usagi_agent.tools import ToolManager, ToolSpec
from .test_tool_runtime import _ProbeTool, _context, _ok


def manager_for(*, required=True, risk="read"):
    manager = ToolManager(approval_store=InMemoryApprovalStore())
    tool = _ProbeTool(spec=ToolSpec(
        name="probe", description="probe", requires_approval=required,
        risk=risk, write_safety=None if risk == "read" else "at_most_once_manual",
    ), behavior=_ok)
    manager.register(tool)
    return manager, tool


def decision(request, value="approve"):
    return {**request.payload(), "expected_approval_version": request.task.version,
            "decision": value}


@pytest.mark.asyncio
async def test_direct_call_defaults_to_approval_and_cannot_forge_a_grant():
    manager, tool = manager_for()
    assert ToolSpec(name="new", description="new").requires_approval
    with pytest.raises(ToolApprovalRequired) as raised:
        await manager.execute(name="probe", arguments={}, context=_context(), operation_id="op")
    assert tool.calls == 0
    grant = decision(raised.value)
    # A caller-provided approve value cannot bypass the pending store record.
    with pytest.raises(ToolApprovalRequired):
        await manager.execute(name="probe", arguments={}, context=_context(), operation_id="op",
                              approval_result=grant)
    assert await manager.approvals.decide(raised.value, grant)
    result = await manager.execute(name="probe", arguments={}, context=_context(), operation_id="op",
                                   approval_result=grant)
    assert result.status == "success" and tool.calls == 1
    changed = await manager.execute(name="probe", arguments={"value": "changed"},
                                   context=_context(), operation_id="op", approval_result=grant)
    assert changed.status == "denied" and tool.calls == 1


@pytest.mark.asyncio
async def test_rejected_call_never_executes():
    manager, tool = manager_for()
    with pytest.raises(ToolApprovalRequired) as raised:
        await manager.execute(name="probe", arguments={}, context=_context(), operation_id="op")
    rejected = decision(raised.value, "reject")
    assert not await manager.approvals.decide(raised.value, rejected)
    # A replay without its saved resume value must interrupt at the same call
    # position; supplying the rejection produces the terminal denial.
    with pytest.raises(ToolApprovalRequired):
        await manager.execute(name="probe", arguments={}, context=_context(), operation_id="op")
    result = await manager.execute(
        name="probe", arguments={}, context=_context(), operation_id="op",
        approval_result=rejected,
    )
    assert result.status == "denied" and tool.calls == 0


@pytest.mark.asyncio
async def test_explicit_opt_out_works_even_for_high_risk_tool():
    manager, tool = manager_for(required=False, risk="high_risk_write")
    result = await manager.execute(name="probe", arguments={}, context=_context())
    assert result.status == "success" and tool.calls == 1


@pytest.mark.asyncio
async def test_missing_approval_infrastructure_fails_closed():
    _, tool = manager_for()
    manager = ToolManager()
    manager.register(tool)
    result = await manager.execute(name="probe", arguments={}, context=_context())
    assert result.status == "denied" and tool.calls == 0


@pytest.mark.asyncio
async def test_multiple_interrupts_preserve_resume_positions_on_node_replay():
    manager, _ = manager_for()
    async def node(state):
        for op in ("first", "second"):
            try:
                await manager.approvals.check(name="probe", arguments={}, context=_context(), operation_id=op)
            except ToolApprovalRequired as request:
                answer = interrupt(request.payload())
                assert await manager.approvals.decide(request, answer)
        return {"done": True}

    graph = StateGraph(dict)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "two-approvals"}}
    result = await compiled.ainvoke({}, config)
    for _ in range(2):
        payload = result["__interrupt__"][0].value
        result = await compiled.ainvoke(Command(resume={
            **payload, "expected_approval_version": payload["approval_version"],
            "decision": "approve",
        }), config)
    assert result["done"] is True
