import pytest
from pydantic import BaseModel, SecretStr

from examples.structured_agent.agent import create_research_writer_agent
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.run import RunStartRequest, ApprovalResume


class Request(BaseModel):
    query: str


def build(path):
    runtime = ServiceRuntimeInitializer.init(BootstrapSettings(
        sqlite_path=str(path / "runtime.db"), model_execution_mode="scripted",
        resume_hmac_key=SecretStr("durable-test-secret-32-characters"),
    ))
    server = Server(runtime)
    runtime.tool_manager.register(SearchToolAdapter())
    create_research_writer_agent(server)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    return server


@pytest.mark.asyncio
async def test_approval_survives_runtime_restart(tmp_path):
    server = build(tmp_path)
    request = RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key="durable",
        input=Request(query="test"),
    )
    handle = await server.create_session(request)
    assert handle.outcome.kind == "suspended", handle.outcome
    descriptor = handle.outcome.interrupts[0]
    token = await server.issue_resume_token(handle.run_id, descriptor.interrupt_id, descriptor.checkpoint_id)
    await server.shutdown()

    server = build(tmp_path)
    approval = await server.runtime.persistence.approval_store.get(descriptor.interrupt_id)
    assert approval.arguments_ref is not None
    result = await server.resume(handle.run_id, ApprovalResume(
        interrupt_id=descriptor.interrupt_id, expected_checkpoint_id=descriptor.checkpoint_id,
        resume_token=token.resume_token, approval_id=approval.approval_id,
        expected_approval_version=approval.version, approval_scope=approval.approval_scope,
        action_hash=approval.action_hash, decision="approve",
    ))
    assert result.outcome.kind == "completed", result.outcome
    from usagi_agent.pipelines.artifacts import get_text
    assert await get_text(server.runtime.persistence.artifact_manager,result.outcome.result_ref.artifact_id)
    replay = await server.create_session(request)
    assert replay.run_id == handle.run_id
    followup = await server.create_session(request.model_copy(update={'request_idempotency_key':'followup'}))
    # A new idempotency key creates a new Session and an isolated Run.
    assert followup.session_id != handle.session_id
    separate = await server.create_session(request.model_copy(update={'request_idempotency_key':'separate'}))
    assert separate.session_id != handle.session_id
    await server.shutdown()


async def _process_phase(phase, directory):
    """Separate interpreters must recover solely from persisted state."""
    import json
    from pathlib import Path
    directory=Path(directory)
    server=build(directory)
    request=RunStartRequest(scenario_key='example.research_writer',
        request_idempotency_key='process-restart',input=Request(query='persistent approval'))
    try:
        if phase=='start':
            handle=await server.create_session(request)
            assert handle.outcome.kind=='suspended'
            descriptor=handle.outcome.interrupts[0]
            token=await server.issue_resume_token(handle.run_id,descriptor.interrupt_id,descriptor.checkpoint_id)
            task=await server.runtime.persistence.approval_store.get(descriptor.interrupt_id)
            resume=ApprovalResume(interrupt_id=descriptor.interrupt_id,
                expected_checkpoint_id=descriptor.checkpoint_id,resume_token=token.resume_token,
                approval_id=task.approval_id,expected_approval_version=task.version,
                approval_scope=task.approval_scope,action_hash=task.action_hash,decision='approve')
            (directory/'handoff.json').write_text(json.dumps({
                'run_id':handle.run_id,'resume':{**resume.model_dump(mode='json'),
                    'resume_token':resume.resume_token.get_secret_value()}}),encoding='utf-8')
        else:
            handoff=json.loads((directory/'handoff.json').read_text(encoding='utf-8'))
            handle=await server.resume(handoff['run_id'],ApprovalResume.model_validate(handoff['resume']))
            assert handle.outcome.kind=='completed',handle.outcome
            from usagi_agent.pipelines.artifacts import get_text
            assert await get_text(server.runtime.persistence.artifact_manager,handle.outcome.result_ref.artifact_id)
            replay=await server.create_session(request)
            assert replay.run_id==handle.run_id
    finally:
        await server.shutdown()


def test_sqlite_approval_across_independent_processes(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    package=Path(__file__).parents[2]
    environment={**os.environ,'PYTHONPATH':os.pathsep.join([
        str(package),str(package/'src'),str(package.parent),*sys.path])}
    code='import asyncio,sys; from tests.tools.test_durable_approval import _process_phase; asyncio.run(_process_phase(sys.argv[1],sys.argv[2]))'
    for phase in ('start','resume'):
        result=subprocess.run([sys.executable,'-c',code,phase,str(tmp_path)],
            env=environment,capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stdout+result.stderr
    for database in (tmp_path/'runtime.db',tmp_path/'runtime.db.artifacts'):
        with sqlite3.connect(database) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
