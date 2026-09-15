"""Live check: deepseek-v4-flash-vision-exp actually receives the image.

Uses an isolated sqlite path so the running httpserver is untouched.
Run: python scripts/vision_live_check.py <path-to-image> [query]
"""

import asyncio
import base64
import sys

from pydantic import BaseModel

from usagi_agent.models import DEFAULT_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.run import RunStartRequest


class _Query(BaseModel):
    query: str


async def main() -> None:
    image_path = sys.argv[1] if len(sys.argv) > 1 else None
    query = sys.argv[2] if len(sys.argv) > 2 else "识别这张图片中的内容，用一句话回答。"

    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="live",
            sqlite_path=".pytest-tmp/vision-live.db",
        )
    )
    server = Server(runtime)
    server.create_agent(
        id="research_writer",
        input_schema="usagi.agent_request@1.0.0",
        model=DEFAULT_MODEL,
    )
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    try:
        parts = []
        if image_path:
            raw = open(image_path, "rb").read()
            parts.append(
                ImageContentPart(
                    media_type="image/jpeg",
                    url="data:image/jpeg;base64," + base64.b64encode(raw).decode(),
                )
            )
        handle = await server.create_session(
            RunStartRequest(
                scenario_key="example.research_writer",
                request_idempotency_key="vision-live-check",
                input=_Query(query=query),
                content_parts=parts,
            )
        )
        outcome = await server.get_run(handle.run_id)
        print("kind:", outcome.kind)
        if outcome.result_ref is not None:
            text = await get_text(runtime.persistence.artifact_manager, outcome.result_ref.artifact_id)
            print("answer:", text)
        else:
            print("outcome:", outcome)
    finally:
        await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
