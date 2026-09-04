"""Structural argument validation against a ToolSpec parameter schema.

Deliberately lightweight (no external dependency): it enforces the subset of
JSON Schema the framework's tool declarations actually use — ``type: object``
with ``properties`` / ``required`` / ``additionalProperties`` / ``enum``.
Anything the validator does not understand is skipped, never guessed.
"""
from __future__ import annotations

from typing import Any

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_arguments(
    schema: dict[str, Any], arguments: dict[str, object]
) -> str | None:
    """Return a safe, model-readable reason when arguments are invalid."""

    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for name in schema.get("required") or ():
        if name not in arguments:
            return f"missing required argument: {name}"
    if schema.get("additionalProperties") is False:
        for name in arguments:
            if name not in properties:
                return f"unexpected argument: {name}"
    for name, value in arguments.items():
        declaration = properties.get(name)
        if not isinstance(declaration, dict):
            continue
        enum = declaration.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            return f"argument {name} must be one of: {', '.join(map(str, enum))}"
        expected = _JSON_TYPES.get(str(declaration.get("type")))
        if expected is None:
            continue
        # bool is a subclass of int; keep it out of number/integer slots.
        if isinstance(value, bool) and bool not in expected:
            return f"argument {name} must be {declaration.get('type')}"
        if not isinstance(value, expected):
            return f"argument {name} must be {declaration.get('type')}"
    return None
