"""Agent-facing output-schema validation errors and helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from usagi_agent.types.json_schema import (
    JsonSchemaError,
    canonical_schema as _canonical_schema,
    compile_validator as _compile_validator,
    validate_schema as _validate_schema,
)

T = TypeVar("T")


class OutputSchemaError(ValueError):
    """A safe public error raised before a model request is made."""

    def __init__(self, message: str, *, reason_code: str = "output_schema.invalid"):
        super().__init__(message)
        self.reason_code = reason_code


def canonical_schema(schema: Mapping[str, object]) -> dict[str, object]:
    return _translate(_canonical_schema, schema)


def validate_schema(schema: Mapping[str, object]) -> dict[str, object]:
    return _translate(_validate_schema, schema)


def compile_validator(schema: Mapping[str, object]):
    return _translate(_compile_validator, schema)


def _translate(operation: Callable[..., T], *args: object) -> T:
    try:
        return operation(*args)
    except JsonSchemaError as exc:
        reason_code = (
            "output_schema.unsupported" if exc.unsupported else "output_schema.invalid"
        )
        raise OutputSchemaError(str(exc), reason_code=reason_code) from exc
