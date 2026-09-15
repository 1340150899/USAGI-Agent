from decimal import Decimal

import pytest
from pydantic import ValidationError

from usagi_agent.agents import AgentManager, AgentManagerInitializer, AgentSpec
from usagi_agent.kernel.context import RunContext
from usagi_agent.models import (
    DEEPSEEK_V4_FLASH_VISION_EXP_MODEL,
    DEFAULT_MODEL,
    GLM_5_2_MODEL,
    GLM_5_3_FLASH_MODEL,
    MODEL_SPECS,
    ModelSpec,
)
from usagi_agent.registry import BootstrapSettings
from usagi_agent.types.action import ToolAction
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelToolCall
from usagi_agent.types.refs import ArtifactRef, PrincipalRef


def test_agent_spec_requires_an_explicit_model():
    assert "prompt" not in AgentSpec.model_fields
    with pytest.raises(ValidationError):
        AgentSpec(  # pyright: ignore[reportCallIssue]
            id="agent", input_schema="input",
        )


def test_model_catalog_is_static_and_not_registered_in_agent_manager():
    manager = AgentManagerInitializer.init(
        BootstrapSettings(model_execution_mode="scripted"),
    )
    assert GLM_5_2_MODEL.provider_model == "glm-5.2"
    assert GLM_5_2_MODEL.base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert GLM_5_3_FLASH_MODEL.provider_model == "glm-5.3-flash"
    assert GLM_5_3_FLASH_MODEL.input_modalities == frozenset({"text", "image"})
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.provider_model == "deepseek-v4-flash-vision-exp"
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.input_modalities == frozenset(
        {"text", "image"}
    )
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.base_url == "https://api.deepseek.com"
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.api_key_env == "DEEPSEEK_API_KEY"
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.context_window == 1_000_000
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL.extra_body == {
        "thinking": {"type": "disabled"}
    }
    assert DEFAULT_MODEL is DEEPSEEK_V4_FLASH_VISION_EXP_MODEL
    assert DEEPSEEK_V4_FLASH_VISION_EXP_MODEL in MODEL_SPECS
    assert not hasattr(manager, "register_model")
    assert not hasattr(manager, "get_model")
    assert not hasattr(manager, "create_agent")
    assert not hasattr(manager, "register_output_schema")


def test_model_boundary_keeps_raw_calls_until_result_process():
    assert "context_update" not in ModelResponse.model_fields
    assert "raw_arguments" in ModelToolCall.model_fields
    assert "raw_arguments" not in ToolAction.model_fields
    assert "arguments_error" in ToolAction.model_fields


@pytest.mark.asyncio
async def test_agent_manager_owns_live_or_scripted_execution_switch(monkeypatch):
    monkeypatch.delenv(GLM_5_2_MODEL.api_key_env, raising=False)
    live = AgentManager(model_execution_mode="live")
    scripted = AgentManager(model_execution_mode="scripted")
    for manager, suffix in ((live, "live"), (scripted, "scripted")):
        manager.register(AgentSpec(
            id=f"agent-{suffix}", input_schema="input", model=GLM_5_2_MODEL
        ))
    assert await live.health() == "degraded"
    assert await scripted.health() == "healthy"


@pytest.mark.asyncio
async def test_agents_hold_models_and_manager_routes_and_accounts_by_agent():
    manager = AgentManager(model_execution_mode="scripted")
    models = (
        ModelSpec(
            id="m1", provider_model="provider-m1", input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("2"),
        ),
        ModelSpec(id="m2", provider_model="provider-m2"),
    )
    for agent_id, model in (("a1", models[0]), ("a2", models[1])):
        manager.register(AgentSpec(
            id=agent_id, input_schema="input", model=model
        ))
    context = RunContext(
        run_id="run", thread_id="thread", tenant_id="tenant",
        principal=PrincipalRef(principal_kind="user", principal_opaque_id="user"),
        authorization_scope=(), deadline=None, fencing_token=1,
    )
    ref = ArtifactRef(artifact_id="context", content_type="application/json")
    for agent_id in ("a1", "a2"):
        await manager.generate(
            agent_id=agent_id,
            request=ModelRequest(
                messages_ref=ref, context_pack_ref=ref, max_output_tokens=100
            ),
            context=context.to_tool_context(),
        )

    assert manager.get("a1").model is models[0]
    assert manager.usage_for_agent("a1").cost == Decimal("0.00002")
