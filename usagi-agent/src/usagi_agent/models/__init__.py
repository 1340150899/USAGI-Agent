"""Static model declarations used by Agent specifications."""

from usagi_agent.models.catalog import (
    DEEPSEEK_V4_FLASH_VISION_EXP_MODEL,
    DEFAULT_MODEL,
    GLM_5_2_MODEL,
    GLM_5_3_FLASH_CODING_PLAN_MODEL,
    GLM_5_3_FLASH_MODEL,
    MODEL_SPECS,
)
from usagi_agent.types.model import ModelSpec

__all__ = [
    "DEEPSEEK_V4_FLASH_VISION_EXP_MODEL",
    "DEFAULT_MODEL",
    "GLM_5_2_MODEL",
    "GLM_5_3_FLASH_CODING_PLAN_MODEL",
    "GLM_5_3_FLASH_MODEL",
    "MODEL_SPECS",
    "ModelSpec",
]
