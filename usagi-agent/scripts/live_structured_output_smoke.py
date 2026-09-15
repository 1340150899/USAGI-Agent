"""Paid end-to-end smoke test for a registry-backed structured Agent result."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from usagi_agent.agents import create_model_adapter
from usagi_agent.agents import OutputSchemaDefinition
from usagi_agent.models import DEEPSEEK_V4_FLASH_VISION_EXP_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_model
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.action import ToolObservation
from usagi_agent.types.run import RunStartRequest

SCHEMA_REF = "live.agent_result@1.0.0"
RESULT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "fail"]},
        "answer": {"type": "string", "minLength": 1},
        "word_count": {"type": "integer", "minimum": 1},
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "uniqueItems": True,
        },
    },
    "required": ["status", "answer", "word_count", "tags"],
    "additionalProperties": False,
}


class LiveRequest(BaseModel):
    query: str


class CapturingAdapter:
    def __init__(self, model_spec) -> None:
        self._adapter = create_model_adapter(model_spec)
        self.requests = []

    async def generate(self, request, context):
        self.requests.append(request)
        return await self._adapter.generate(request, context)

    async def health(self):
        return await self._adapter.health()

    async def shutdown(self):
        await self._adapter.shutdown()


async def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="usagi-live-structured-", ignore_cleanup_errors=True
    ) as directory:
        runtime = ServiceRuntimeInitializer.init(
            BootstrapSettings(
                model_execution_mode="live",
                sqlite_path=str(Path(directory) / "runtime.db"),
            )
        )
        server = Server(runtime)
        agent = server.create_agent(
            id="research_writer",
            input_schema="live.agent_input@1.0.0",
            output_schema=OutputSchemaDefinition(
                ref=SCHEMA_REF,
                name="live_agent_result",
                schema=RESULT_SCHEMA,
            ),
            model=DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.model_copy(
                update={"default_max_output_tokens": 512}
            ),
        )
        contract = runtime.agent_manager.output_contract(agent.id)
        assert contract is not None
        adapter = CapturingAdapter(DEEPSEEK_V4_FLASH_VISION_EXP_MODEL)
        runtime.agent_manager.set_model_adapter(adapter)
        ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
        try:
            result = await server.create_session(
                RunStartRequest(
                    scenario_key="example.research_writer",
                    request_idempotency_key="live-structured-output-v1",
                    input=LiveRequest(
                        query=(
                            "请用一句简短中文解释 Agent，并将最终结果提交给 "
                            "structured_output。word_count 填写 answer 的大致词数，"
                            "tags 填写至少一个主题标签。"
                        )
                    ),
                )
            )
            executions = await runtime.persistence.tool_execution_store.list_by_run(
                result.run_id
            )
            terminal_records = [
                record
                for record in executions
                if record.tool_name == contract.terminal_tool_name
            ]
            observation = None
            if result.outcome.kind == "completed":
                observation = await get_model(
                    runtime.persistence.artifact_manager,
                    result.outcome.result_ref.artifact_id,
                    ToolObservation,
                )
            terminal_spec = runtime.tool_manager.get_spec(contract.terminal_tool_name)
            report = {
                "outcome": result.outcome.kind,
                "run_id": result.run_id,
                "session_id": result.session_id,
                "model_calls": len(adapter.requests),
                "model_response_formats": [
                    request.response_format for request in adapter.requests
                ],
                "terminal_tool_exposed": all(
                    any(
                        tool.get("function", {}).get("name")
                        == contract.terminal_tool_name
                        for tool in request.tools
                    )
                    for request in adapter.requests
                ),
                "tool_parameters_match_schema": terminal_spec.parameters
                == contract.json_schema,
                "contract_checksum": (
                    contract.schema_checksum if contract else None
                ),
                "terminal_execution_statuses": [
                    record.execution_status for record in terminal_records
                ],
                "observation_status": observation.status if observation else None,
                "observation_accepted": (
                    (observation.output or {}).get("accepted")
                    if observation
                    else None
                ),
                "structured_output": result.structured_output,
                "display_message": result.message,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            checks = (
                report["outcome"] == "completed",
                report["terminal_tool_exposed"] is True,
                report["tool_parameters_match_schema"] is True,
                report["terminal_execution_statuses"] == ["settled_success"],
                report["observation_status"] == "success",
                report["observation_accepted"] is True,
                isinstance(result.structured_output, dict),
                result.structured_output.get("status") == "success"
                if result.structured_output
                else False,
            )
            if not all(checks):
                raise RuntimeError("live structured-output assertions failed")
        finally:
            await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
