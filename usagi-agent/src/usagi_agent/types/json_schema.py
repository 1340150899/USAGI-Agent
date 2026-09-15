"""Dependency-neutral JSON Schema normalization and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

MAX_SCHEMA_BYTES = 64_000
MAX_SCHEMA_DEPTH = 32
MAX_PROPERTIES = 1_000
MAX_DESCRIPTION_LENGTH = 4_000
SUPPORTED_DRAFT = "https://json-schema.org/draft/2020-12/schema"


class JsonSchemaError(ValueError):
    def __init__(self, message: str, *, unsupported: bool = False) -> None:
        super().__init__(message)
        self.unsupported = unsupported


def canonical_schema(schema: Mapping[str, object]) -> dict[str, object]:
    try:
        payload = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JsonSchemaError("schema must be JSON serializable") from exc
    if len(payload) > MAX_SCHEMA_BYTES:
        raise JsonSchemaError(
            f"schema exceeds {MAX_SCHEMA_BYTES} bytes", unsupported=True
        )
    return json.loads(payload)


def validate_schema(schema: Mapping[str, object]) -> dict[str, object]:
    normalized = canonical_schema(schema)
    if normalized.get("type") != "object":
        raise JsonSchemaError("schema root type must be object")
    declared_draft = normalized.get("$schema")
    if declared_draft not in (None, SUPPORTED_DRAFT, SUPPORTED_DRAFT + "#"):
        raise JsonSchemaError(
            "only JSON Schema Draft 2020-12 is supported", unsupported=True
        )
    _check_limits(normalized)
    try:
        Draft202012Validator.check_schema(normalized)
    except SchemaError as exc:
        raise JsonSchemaError(_safe_schema_error(exc)) from exc
    return normalized


def compile_validator(schema: Mapping[str, object]):
    return Draft202012Validator(validate_schema(schema))


def format_validation_error(error: ValidationError) -> str:
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    actual = type(error.instance).__name__
    rule = error.validator or "schema"
    expected = error.validator_value
    if rule == "type":
        detail = f"expected {expected}; got {actual}"
    elif rule == "enum":
        detail = "value is not in enum"
    elif rule == "required":
        detail = "required property is missing"
    elif rule == "additionalProperties":
        detail = "additional property is not allowed"
    else:
        detail = f"violates {rule}"
    return f"{path}: {detail}"[:500]


def _check_limits(value: object, *, depth: int = 0) -> int:
    if depth > MAX_SCHEMA_DEPTH:
        raise JsonSchemaError(
            f"schema exceeds maximum depth {MAX_SCHEMA_DEPTH}", unsupported=True
        )
    property_count = 0
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            property_count += len(properties)
        description = value.get("description")
        if isinstance(description, str) and len(description) > MAX_DESCRIPTION_LENGTH:
            raise JsonSchemaError("schema description is too long", unsupported=True)
        ref = value.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#/"):
            raise JsonSchemaError(
                "remote or non-local $ref is not supported", unsupported=True
            )
        for child in value.values():
            property_count += _check_limits(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            property_count += _check_limits(child, depth=depth + 1)
    if property_count > MAX_PROPERTIES:
        raise JsonSchemaError(
            f"schema exceeds {MAX_PROPERTIES} properties", unsupported=True
        )
    return property_count


def _safe_schema_error(error: SchemaError) -> str:
    path = "$" + "".join(f".{part}" for part in error.absolute_path)
    return f"{path}: invalid JSON Schema"[:500]
