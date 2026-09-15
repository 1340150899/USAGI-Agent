"""Agent-owned registry for compiled output schemas."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from usagi_agent.agents.schemas.types import OutputContract
from usagi_agent.agents.schemas.validation import canonical_schema, compile_validator


class OutputSchemaManager:
    def __init__(self) -> None:
        self._contracts: dict[str, OutputContract] = {}
        self._validators: dict[str, object] = {}

    def register(
        self,
        ref: str,
        *,
        name: str,
        schema: Mapping[str, object],
        max_retries: int = 5,
    ) -> OutputContract:
        normalized = canonical_schema(schema)
        validator = compile_validator(normalized)
        payload = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        checksum = hashlib.sha256(payload).hexdigest()
        tool_key = hashlib.sha256(f"{ref}:{name}".encode("utf-8")).hexdigest()[:12]
        contract = OutputContract(
            name=name,
            schema=normalized,
            schema_checksum=checksum,
            terminal_tool_name=f"structured_output_{tool_key}_{checksum[:12]}",
            max_retries=max_retries,
        )
        self._contracts[ref] = contract
        self._validators[ref] = validator
        return contract

    def resolve(self, ref: str) -> OutputContract:
        try:
            return self._contracts[ref]
        except KeyError as exc:
            raise KeyError(f"unknown output schema ref: {ref}") from exc
