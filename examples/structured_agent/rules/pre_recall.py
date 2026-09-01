from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.rules import (
    PreRecallAdapterConfig,
    PreRecallRuleInput,
    PreRecallRuleOutput,
)


class ResearchWriterPreRecallConfig(PreRecallAdapterConfig):
    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context: RunContext
    ) -> PreRecallRuleOutput:
        return PreRecallRuleOutput(normalized_input_ref=input.request_ref)
