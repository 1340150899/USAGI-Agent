from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules import ContextBuildAdapterConfig, StatePatch


class ResearchWriterContextBuildConfig(ContextBuildAdapterConfig):
    async def build_context(self, state: AgentRunState, runtime, context: RunContext) -> StatePatch:
        return {"context_pack_ref": state.get("normalized_input_ref", "")}
