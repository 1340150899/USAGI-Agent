from datetime import datetime, timezone

from usagi_agent.ports import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.tools.spec import CURRENT_TIME_SPEC


class CurrentTimeTool(ToolAdapter):
    spec = CURRENT_TIME_SPEC

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        return {"current_time": datetime.now(timezone.utc).isoformat()}
