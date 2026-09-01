from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules import RecallAdapterConfig, StatePatch


class ResearchWriterRecallConfig(RecallAdapterConfig):
    async def recall(self, state: AgentRunState, runtime, context: RunContext) -> StatePatch:
        return {"recall_cache": {}}
