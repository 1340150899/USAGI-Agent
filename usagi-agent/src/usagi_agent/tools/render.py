"""Model-facing rendering of tool observations (design §21.6).

The model sees status plus business output or a safe reason — never latency,
receipt refs, artifact refs or raw exception text. Audit fields stay in the
persisted observation artifact only.
"""
from __future__ import annotations

import json

from usagi_agent.types.action import ToolObservation


def render_model_content(observation: ToolObservation) -> str:
    if observation.status == "success":
        payload: dict[str, object] = {
            "status": "success",
            "output": observation.output,
        }
    else:
        payload = {
            "status": observation.status,
            "error_code": observation.error_code,
            "reason": observation.error_message,
        }
    return json.dumps(payload, ensure_ascii=False, default=str)
