from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import BaseModel

from usagi_agent.agents.schemas import OutputSchemaDefinition, OutputSchemaError
from usagi_agent.models import GLM_5_2_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.model import ModelResponse, ModelToolCall
from usagi_agent.types.run import RunStartRequest

SCHEMA_REF = "test.result@1.0.0"
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "fail"]},
        "message": {"type": "string", "minLength": 1},
    },
    "required": ["status", "message"],
    "additionalProperties": False,
}
class Query(BaseModel):
    query: str


def _runtime(tmp_path):
    return ServiceRuntimeInitializer.init(BootstrapSettings(
        model_execution_mode="scripted",
        sqlite_path=str(tmp_path / "runtime.db"),
    ))


def _schema_definition(schema=RESULT_SCHEMA, *, name="result"):
    return OutputSchemaDefinition(ref=SCHEMA_REF, name=name, schema=schema)


def _register_agent(server, schema=RESULT_SCHEMA):
    agent = server.create_agent(
        id="research_writer",
        input_schema="input",
        output_schema=_schema_definition(schema),
        model=GLM_5_2_MODEL,
    )
    contract = server.runtime.agent_manager.output_contract(agent.id)
    assert contract is not None
    assert contract.terminal_tool_name in agent.allowed_tools
    return contract


@pytest.mark.asyncio
async def test_create_agent_compiles_and_registers_output_schema(tmp_path):
    runtime = _runtime(tmp_path)
    server = Server(runtime)
    left = _register_agent(server)
    second = server.create_agent(
        id="second_writer",
        input_schema="input",
        output_schema=_schema_definition(
            dict(reversed(list(RESULT_SCHEMA.items())))
        ),
        model=GLM_5_2_MODEL,
    )
    right = runtime.agent_manager.output_contract(second.id)
    assert right is not None
    assert left.schema_checksum == right.schema_checksum
    assert not hasattr(server, "register_output_schema")
    with pytest.raises(OutputSchemaError, match="root type"):
        server.create_agent(
            id="bad-root",
            input_schema="input",
            output_schema=OutputSchemaDefinition(
                ref="bad", name="bad", schema={"type": "array"}
            ),
            model=GLM_5_2_MODEL,
        )
    with pytest.raises(OutputSchemaError, match="remote"):
        server.create_agent(
            id="bad-ref",
            input_schema="input",
            output_schema=OutputSchemaDefinition(
                ref="bad",
                name="bad",
                schema={"type": "object", "$ref": "https://bad/schema"},
            ),
            model=GLM_5_2_MODEL,
        )
    assert (
        runtime.tool_manager.get_spec(left.terminal_tool_name).parameters
        == RESULT_SCHEMA
    )
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_output_schema_is_not_added_to_session_table(tmp_path):
    runtime = _runtime(tmp_path)
    server = Server(runtime)
    _register_agent(server)
    await runtime.session_manager.create("user", "session")
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert "output_contract" not in columns
    await runtime.shutdown()


class SchemaAwareModel:
    def __init__(self, *, repair_first: bool = False):
        self.schemas = []
        self.repair_first = repair_first

    async def generate(self, request, context):
        assert request.response_format == "text"
        tool = next(
            item for item in request.tools
            if item["function"]["name"].startswith("structured_output_")
        )
        schema = tool["function"]["parameters"]
        self.schemas.append(schema)
        if self.repair_first and len(self.schemas) == 1:
            arguments = {"status": "invalid", "message": "bad"}
        else:
            arguments = {"status": "success", "message": "done"}
        return ModelResponse(tool_calls=[ModelToolCall(
            tool_name=tool["function"]["name"],
            tool_call_id=f"structured-{len(self.schemas)}",
            raw_arguments=json.dumps(arguments),
        )])

    async def health(self):
        return "healthy"


@pytest.mark.asyncio
async def test_invalid_submission_is_observed_then_valid_result_completes(tmp_path):
    runtime = _runtime(tmp_path)
    server = Server(runtime)
    model = SchemaAwareModel(repair_first=True)
    runtime.agent_manager.set_model_adapter(model)
    _register_agent(server)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    result = await server.create_session(RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key="structured-run",
        input=Query(query="finish"),
    ))
    assert result.outcome.kind == "completed"
    assert result.structured_output == {"status": "success", "message": "done"}
    assert result.message == "done"
    assert len(model.schemas) == 2
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        persisted_payloads = b"".join(
            row[0] for row in db.execute("SELECT payload FROM artifact_blobs")
        )
    assert b'"additionalProperties":false' in persisted_payloads
    await server.shutdown()
