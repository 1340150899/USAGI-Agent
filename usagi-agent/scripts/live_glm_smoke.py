"""Run a paid, end-to-end GLM smoke test without persisting the API key."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from usagi_agent.agents import create_model_adapter
from usagi_agent.models import GLM_5_3_FLASH_MODEL
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processor import PipelineProcessor
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.run import RunOptions, RunStartRequest


class SmokeRequest(BaseModel):
    query: str


class _VisibleLiveAdapter:
    """Expose provider failures that KernelRuntime otherwise projects as run.failed."""

    adapter_ref = "usagi.live_smoke_visible_adapter"

    def __init__(self, model_spec) -> None:
        self._adapter = create_model_adapter(model_spec)

    async def generate(self, request, ctx):
        try:
            return await self._adapter.generate(request, ctx)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "model_error_type": type(exc).__name__,
                        "model_error": str(exc)[:2_000],
                    },
                    ensure_ascii=False,
                )
            )
            raise

    async def health(self):
        return await self._adapter.health()

    async def shutdown(self):
        await self._adapter.shutdown()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="usagi-live-smoke-") as temp_dir:
        runtime = ServiceRuntimeInitializer.init(
            BootstrapSettings(
                model_execution_mode="live",
                memory_path=str(Path(temp_dir) / "memory.json"),
            )
        )
        agent = runtime.agent_manager.create_agent(
            id="research_writer",
            input_schema="usagi.agent_request@1.0.0",
            output_schema="usagi.final_output@1.0.0",
            model=GLM_5_3_FLASH_MODEL.model_copy(
                update={
                    # A small framework budget triggers the real compaction path,
                    # while still fitting one prompt-bounded compaction batch.
                    "context_window": 2_400,
                    "default_max_output_tokens": 512,
                }
            ),
            allowed_tools=("current_time",),
        )
        runtime.agent_manager.set_model_adapter(
            _VisibleLiveAdapter(agent.model)
        )
        ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
        server = Server(runtime)
        request = RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key="live-glm-tool-compaction-final-v1",
            input=SmokeRequest(
                query=(
                    "请调用且只调用一次 current_time 工具，然后根据工具结果给出当前"
                    "UTC 时间。工具已经调用成功后不要重复调用。以下内容仅用于触发上下文"
                    "压缩，不需要复述："
                    + ("A" * 2_500)
                )
            ),
            options=RunOptions(),
        )
        handle = await server.start_agent(request)
        outcome = await server.get_run(handle.run_id)
        snapshot = await runtime.persistence.execution_context_store.get(handle.run_id)
        control = await runtime.persistence.run_control_store.get(handle.run_id)
        if snapshot is None or control is None:
            raise RuntimeError("missing execution state after live smoke test")
        tool_context = ToolContext(
            execution=GovernedExecutionContext(
                tenant_id=snapshot.tenant_id,
                principal=snapshot.original_principal,
                authorization_scope=snapshot.authorization_scope,
                control_kind="run",
                control_id=handle.run_id,
                fencing_token=control.fencing_token,
            )
        )
        session = await runtime.memory_manager.get_session_context(
            handle.thread_id, tool_context
        )
        events = await runtime.memory_manager.list_events(
            handle.thread_id, tool_context
        )
        memories = await runtime.memory_manager.get_long_term_memories(
            RecallQuery(query_id="live-smoke", text="current_time"),
            tool_context,
        )
        final_text = ""
        if outcome.kind == "completed":
            final_text = await get_text(
                runtime.persistence.artifact_manager,
                outcome.result_ref.artifact_id,
            )
        usage = runtime.agent_manager.usage_for_agent("research_writer")
        print(
            json.dumps(
                {
                    "outcome": outcome.kind,
                    "final_text": final_text,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "event_roles": [event.role for event in events],
                    "compacted": session.compacted_until is not None,
                    "compaction_extracted": (
                        session.compacted_until == session.extracted_until
                        and session.compacted_until is not None
                    ),
                    "long_term_memory_hits": len(memories),
                },
                ensure_ascii=False,
            )
        )
        scenario = runtime.scenario_registry.get("example.research_writer")
        processor = PipelineProcessor(scenario.config, runtime)
        compaction_context = RunContext(
            run_id="live-compaction-run",
            thread_id="live-compaction-thread",
            tenant_id="default",
            principal=PrincipalRef(
                principal_kind="user",
                principal_opaque_id="live-compaction-user",
            ),
            authorization_scope=(),
            deadline=None,
            fencing_token=1,
        )
        compaction_tool_context = compaction_context.to_tool_context()
        for role, content in (
            (
                "user",
                "Stable user preference: always use metric units. "
                + ("B" * 1_200),
            ),
            ("assistant", "Earlier assistant analysis: " + ("C" * 1_200)),
            (
                "user",
                "上下文压缩完成后直接简短回答“压缩链路正常”，不要调用工具。",
            ),
        ):
            await runtime.memory_manager.append_event(
                session_id=compaction_context.thread_id,
                role=role,
                content=content,
                ctx=compaction_tool_context,
            )
        state: AgentRunState = {"context_compaction_mode": "force"}
        state.update(
            cast(
                AgentRunState,
                await processor.process_context_build(state, compaction_context),
            )
        )
        if state.get("context_operation") != "compaction":
            raise RuntimeError("live compaction request was not selected")
        state.update(
            cast(
                AgentRunState,
                await processor.process_model(state, compaction_context),
            )
        )
        state.update(
            cast(
                AgentRunState,
                await processor.process_result(state, compaction_context),
            )
        )
        state.update(
            cast(
                AgentRunState,
                await processor.process_end(state, compaction_context),
            )
        )
        if state.get("pass_disposition") != "next_pass":
            raise RuntimeError("live compaction did not request another pass")
        state.update(
            cast(
                AgentRunState,
                await processor.process_context_build(state, compaction_context),
            )
        )
        if state.get("context_operation") != "normal":
            raise RuntimeError("compacted context was not used for the next model call")
        state.update(
            cast(
                AgentRunState,
                await processor.process_model(state, compaction_context),
            )
        )
        state.update(
            cast(
                AgentRunState,
                await processor.process_result(state, compaction_context),
            )
        )
        state.update(
            cast(
                AgentRunState,
                await processor.process_end(state, compaction_context),
            )
        )
        compacted_session = await runtime.memory_manager.get_session_context(
            compaction_context.thread_id, compaction_tool_context
        )
        compacted_memories = await runtime.memory_manager.get_long_term_memories(
            RecallQuery(query_id="live-compaction-memory", text="metric units"),
            compaction_tool_context,
        )
        compacted_final = await get_text(
            runtime.persistence.artifact_manager,
            state.get("final_output_ref", ""),
        )
        print(
            json.dumps(
                {
                    "compaction_next_outcome": state.get("pass_disposition"),
                    "compaction_final_text": compacted_final,
                    "compacted": compacted_session.compacted_until is not None,
                    "compaction_extracted": (
                        compacted_session.compacted_until
                        == compacted_session.extracted_until
                        and compacted_session.compacted_until is not None
                    ),
                    "structured_context_present": bool(
                        compacted_session.summary
                        or compacted_session.facts
                        or compacted_session.goals
                    ),
                    "long_term_memory_hits": len(compacted_memories),
                },
                ensure_ascii=False,
            )
        )
        await server.shutdown()
        if outcome.kind != "completed":
            raise RuntimeError("live GLM smoke test did not complete")
        if state.get("pass_disposition") != "run_completed":
            raise RuntimeError("live compacted follow-up did not complete")
        if not compacted_memories:
            raise RuntimeError("live compaction did not extract durable memory")


if __name__ == "__main__":
    asyncio.run(main())
