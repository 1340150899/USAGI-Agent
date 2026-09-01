from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules import PreRecallAdapterConfig, StatePatch


class ResearchWriterPreRecallConfig(PreRecallAdapterConfig):
    async def pre_recall(self, state: AgentRunState, runtime, context: RunContext) -> StatePatch:
        return {"normalized_input_ref": state.get("request_ref", "")}
