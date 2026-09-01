from datetime import datetime, timezone

from usagi_agent.ports import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.tool import ToolSpec


class CurrentTimeTool(ToolAdapter):
    spec = ToolSpec(
        name="current_time",
        description="Get the current server date and time in UTC.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        return {"current_time": datetime.now(timezone.utc).isoformat()}
