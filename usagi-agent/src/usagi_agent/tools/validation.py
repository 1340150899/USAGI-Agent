"""JSON-Schema validation shared by every registered tool."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, cast

from usagi_agent.types.json_schema import (
    compile_validator,
    format_validation_error,
    validate_schema,
)


def validate_parameter_schema(schema: dict[str, Any]) -> dict[str, object]:
    return validate_schema(schema)


def validate_arguments(
    schema: dict[str, Any], arguments: dict[str, object]
) -> str | None:
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    validator = _compiled_validator(payload)
    errors = sorted(
        validator.iter_errors(cast(Any, arguments)),
        key=lambda error: (0 if error.validator == "required" else 1, list(error.path)),
    )
    if not errors:
        return None
    rendered: list[str] = []
    for error in errors[:8]:
        if error.validator == "required":
            missing = next(
                (name for name in error.validator_value if name not in error.instance),
                "unknown",
            )
            rendered.append(f"missing required argument: {missing}")
        elif error.validator == "additionalProperties":
            properties = schema.get("properties") or {}
            unexpected = next(
                (name for name in arguments if name not in properties), "unknown"
            )
            rendered.append(f"unexpected argument: {unexpected}")
        else:
            rendered.append(format_validation_error(error))
    return "; ".join(rendered)[:2_000]


@lru_cache(maxsize=256)
def _compiled_validator(payload: str):
    return compile_validator(json.loads(payload))
