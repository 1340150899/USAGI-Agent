from usagi_agent.ports import HealthStatus, ModelAdapter, ToolContext
from usagi_agent.types.action import ToolAction
from usagi_agent.types.model import ModelRequest, ModelResponse
from usagi_agent.types.refs import ArtifactRef


class ScriptedModelAdapter(ModelAdapter):
    adapter_ref = "adapter.scripted_model"

    def __init__(self) -> None:
        self._tool_used_by_run: set[str] = set()
        self.seen_contexts: dict[str, ToolContext] = {}

    async def generate(self, request: ModelRequest, ctx: ToolContext) -> ModelResponse:
        run_id = ctx.execution.control_id
        self.seen_contexts[run_id] = ctx
        if run_id not in self._tool_used_by_run:
            self._tool_used_by_run.add(run_id)
            return ModelResponse(
                content_ref=ArtifactRef(artifact_id="placeholder", content_type="text/plain"),
                tool_calls=[ToolAction(
                    tool_name="web_search",
                    tool_call_id=f"tc_{run_id}",
                    arguments={"query": "agent frameworks"},
                    rationale="gather sources",
                )],
                finish_reason="tool_use",
            )
        return ModelResponse(
            content_ref=ArtifactRef(artifact_id="final_output_content", content_type="text/plain"),
            finish_reason="stop",
        )

    async def health(self) -> HealthStatus:
        return "healthy"
