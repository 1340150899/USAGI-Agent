"""Run with: python -m examples.structured_agent.run"""
import asyncio

from pydantic import BaseModel

from examples.structured_agent.agent import RESEARCH_WRITER_AGENT
from examples.structured_agent.configs import SCENARIO_CONFIGS
from examples.structured_agent.model_adapter import ScriptedModelAdapter
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.registry.bootstrap import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.run import RunOptions, RunStartRequest


class ResearchRequest(BaseModel):
    query: str


def build_server(model_adapter: ScriptedModelAdapter | None = None) -> Server:
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=model_adapter or ScriptedModelAdapter()
    )
    runtime.tool_manager.register(SearchToolAdapter())
    runtime.agent_manager.register(RESEARCH_WRITER_AGENT)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    return Server(runtime)


async def main() -> None:
    server = build_server()
    request = RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key="demo-run-1",
        input=ResearchRequest(query="summarize agent frameworks"),
        options=RunOptions(),
    )
    handle = await server.start_agent(request)
    outcome = await server.get_run(handle.run_id)
    print(f"run_id={handle.run_id} outcome={outcome.kind}")
    await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
