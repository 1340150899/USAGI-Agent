from pydantic import BaseModel
from pydantic import ValidationError
import pytest

from examples.structured_agent.run import build_server
from usagi_agent.kernel import AuthContext
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.run import RunStartRequest


class Query(BaseModel):
    query: str


def auth(uid="user"):
    return AuthContext(
        principal=PrincipalRef(principal_kind="user", principal_opaque_id=uid),
        authorization_scope=("run.start", "run.execute", "run.read", "run.resume"),
    )


def request(key, text):
    return RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key=key,
        input=Query(query=text),
    )


def test_session_identity_is_runtime_owned():
    with pytest.raises(ValidationError):
        RunStartRequest.model_validate(
            {
                "scenario_key": "example.research_writer",
                "request_idempotency_key": "client-session",
                "session_key": "not-allowed",
                "input": Query(query="research"),
            }
        )


@pytest.mark.asyncio
async def test_regular_turns_keep_one_session_and_memory_thread(tmp_path):
    server = build_server(tmp_path / "runtime.db")
    try:
        first = await server.create_session(request("first", "research one"), auth=auth())
        turns = [first]
        for number in range(2, 6):
            turns.append(await server.continue_session(
                first.session_id,
                request(f"turn-{number}", f"research {number}"),
                auth=auth(),
            ))
        assert all(turn.outcome.kind == "completed" for turn in turns)
        assert all(turn.session_id == first.session_id for turn in turns)
        assert len({turn.run_id for turn in turns}) == len(turns)
        assert (await server.runtime.session_manager.get(first.session_id)).status == "idle"
        assert not hasattr(server, "start_agent")
    finally:
        await server.shutdown()


@pytest.mark.asyncio
async def test_pending_session_accepts_only_yes_or_no(tmp_path):
    server = build_server(tmp_path / "runtime.db", requires_approval=True)
    try:
        first = await server.create_session(request("first", "research"), auth=auth())
        assert first.outcome.kind == "suspended"
        assert "请回复“Yes”或“No”" in first.message

        repeated = await server.continue_session(
            first.session_id, request("not-a-decision", "later"), auth=auth()
        )
        assert repeated.outcome.kind == "suspended"
        assert repeated.message == first.message

        approved = await server.continue_session(
            first.session_id, request("approval", " yEs "), auth=auth()
        )
        assert approved.outcome.kind == "completed"
        assert approved.session_id == first.session_id
        assert (await server.runtime.session_manager.get(first.session_id)).status == "idle"
    finally:
        await server.shutdown()
