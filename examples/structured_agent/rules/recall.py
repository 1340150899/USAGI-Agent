from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.rules import RecallAdapterConfig, RecallRuleInput, RecallRuleOutput


class ResearchWriterRecallConfig(RecallAdapterConfig):
    async def recall(
        self, input: RecallRuleInput, runtime, context: RunContext
    ) -> RecallRuleOutput:
        return RecallRuleOutput(recall_cache={})
