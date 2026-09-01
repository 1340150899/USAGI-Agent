from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.artifacts import put_model
from usagi_agent.pipelines.rules import ModelRuleAdapterConfig, ModelRuleInput, ModelRuleOutput
from usagi_agent.tools import to_model_tool
from usagi_agent.types.model import ModelRequest
from usagi_agent.types.refs import ArtifactRef


class ResearchWriterModelConfig(ModelRuleAdapterConfig):
    agent_id: str = "research_writer"

    async def model(
        self, input: ModelRuleInput, runtime, context: RunContext
    ) -> ModelRuleOutput:
        agent = runtime.agent_manager.get(self.agent_id)
        prompt_ref, _ = runtime.agent_manager.get_prompt(self.agent_id)
        system_prompt = runtime.agent_manager.render_prompt(self.agent_id)
        tools = [to_model_tool(spec) for spec in runtime.tool_manager.get_specs(agent.allowed_tools)]
        context_ref = ArtifactRef(
            artifact_id=input.context_pack_ref, content_type="application/json"
        )
        response = await runtime.agent_manager.generate(
            agent_id=self.agent_id,
            request=ModelRequest(
                model_id=self.agent_id,
                prompt_ref=prompt_ref,
                system_prompt=system_prompt,
                messages_ref=context_ref,
                context_pack_ref=context_ref,
                tools=tools,
            ),
            context=context,
        )
        ref = await put_model(
            runtime.persistence.artifact_manager,
            response,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"model:{context.run_id}:{input.iteration}",
        )
        return ModelRuleOutput(model_response_ref=ref)
