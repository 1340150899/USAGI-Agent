from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.rules import (
    ContextBuildAdapterConfig,
    ContextBuildRuleInput,
    ContextBuildRuleOutput,
)


class ResearchWriterContextBuildConfig(ContextBuildAdapterConfig):
    async def build_context(
        self, input: ContextBuildRuleInput, runtime, context: RunContext
    ) -> ContextBuildRuleOutput:
        return ContextBuildRuleOutput(context_pack_ref=input.normalized_input_ref)
