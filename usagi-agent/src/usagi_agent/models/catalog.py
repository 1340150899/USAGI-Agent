"""Single source of truth for the framework's immutable model declarations."""

from usagi_agent.types.model import ModelSpec

DEEPSEEK_V4_FLASH_VISION_EXP_MODEL = ModelSpec(
    id="deepseek-v4-flash-vision-exp",
    provider_model="deepseek-v4-flash-vision-exp",
    input_modalities=frozenset({"text", "image"}),
    base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    context_window=1_000_000,
    default_max_output_tokens=8_192,
    extra_body={"thinking": {"type": "disabled"}},
)

GLM_5_2_MODEL = ModelSpec(
    id="glm-5.2",
    provider_model="glm-5.2",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key_env="GLM_API_KEY",
    context_window=128_000,
    default_max_output_tokens=8_192,
)

GLM_5_3_FLASH_MODEL = ModelSpec(
    id="glm-5.3-flash",
    provider_model="glm-5.3-flash",
    input_modalities=frozenset({"text", "image"}),
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key_env="GLM_API_KEY",
    context_window=1_000_000,
    default_max_output_tokens=8_192,
)

GLM_5_3_FLASH_CODING_PLAN_MODEL = ModelSpec(
    id="glm-5.3-flash-coding-plan",
    provider_model="glm-5.3-flash",
    protocol="openai_responses",
    input_modalities=frozenset({"text", "image"}),
    base_url="https://open.bigmodel.cn/api/v1",
    api_key_env="GLM_API_KEY",
    context_window=1_000_000,
    default_max_output_tokens=8_192,
)

MODEL_SPECS: tuple[ModelSpec, ...] = (
    DEEPSEEK_V4_FLASH_VISION_EXP_MODEL,
    GLM_5_2_MODEL,
    GLM_5_3_FLASH_MODEL,
    GLM_5_3_FLASH_CODING_PLAN_MODEL,
)

DEFAULT_MODEL = DEEPSEEK_V4_FLASH_VISION_EXP_MODEL

__all__ = [
    "DEEPSEEK_V4_FLASH_VISION_EXP_MODEL",
    "DEFAULT_MODEL",
    "GLM_5_2_MODEL",
    "GLM_5_3_FLASH_CODING_PLAN_MODEL",
    "GLM_5_3_FLASH_MODEL",
    "MODEL_SPECS",
]
