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


def build_server(
    database_path: str | Path,
    *,
    use_scripted_model: bool = True,
    requires_approval: bool = False,
) -> Server:
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            sqlite_path=str(database_path),
            model_execution_mode="scripted" if use_scripted_model else "live"
        )
    )
    tool = SearchToolAdapter()
    tool.spec = tool.spec.model_copy(update={"requires_approval": requires_approval})
    runtime.tool_manager.register(tool)
    create_research_writer_agent(runtime.agent_manager)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    return Server(runtime)


async def main() -> None:
    server = build_server(Path(".usagi/example-runtime.db"))
    request = RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key="demo-run-1",
        input=ResearchRequest(query="summarize agent frameworks"),
        options=RunOptions(),
    )
    message = await server.create_session(request)
    outcome = await server.get_run(message.run_id)
    print(f"run_id={message.run_id} outcome={outcome.kind}")
    await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
