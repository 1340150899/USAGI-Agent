"""Run with: python -m examples.structured_agent.run"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2] / "usagi-agent"
for path in (PROJECT_ROOT / "src", PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pydantic import BaseModel

from examples.structured_agent.agent import create_research_writer_agent
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.registry.bootstrap import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.run import RunOptions, RunStartRequest


class ResearchRequest(BaseModel):
    query: str


def build_server(*, use_scripted_model: bool = True) -> Server:
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="scripted" if use_scripted_model else "live"
        )
    )
    runtime.tool_manager.register(SearchToolAdapter())
    create_research_writer_agent(runtime.agent_manager)
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
