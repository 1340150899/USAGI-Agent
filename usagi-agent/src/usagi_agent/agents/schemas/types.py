"""Immutable JSON-Schema output contracts owned by Agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.refs import SchemaRef


class OutputSchemaDefinition(BaseModel):
    """Application declaration used when creating or updating an Agent."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    ref: SchemaRef
    name: str
    json_schema: Mapping[str, object] = Field(alias="schema")
    max_retries: int = Field(default=5, ge=0, le=20)


class OutputContract(BaseModel):
    """The compiled final-output contract referenced by an AgentSpec."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True
    )

    type: Literal["json_schema"] = "json_schema"
    name: str
    json_schema: dict[str, object] = Field(alias="schema")
    schema_checksum: str
    terminal_tool_name: str
    max_retries: int = Field(default=5, ge=0, le=20)
