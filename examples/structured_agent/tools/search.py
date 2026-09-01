from usagi_agent.ports import ToolContext
from usagi_agent.tools import ToolAdapter, ToolSpec


class SearchToolAdapter(ToolAdapter):
    spec = ToolSpec(
        name="web_search",
        description="Search the web for current information.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        query = str(arguments["query"])
        return {"query": query, "summary": "found relevant sources"}
