"""Provider-neutral multimodal content contracts and common operations.

Binary payloads stay in the Artifact data plane.  Graph state and memory only
carry opaque ``ArtifactRef`` values or remote URLs.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from usagi_agent.types.refs import ArtifactRef


class TextContentPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text"] = "text"
    text: str


class ImageContentPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["image"] = "image"
    media_type: str = Field(pattern=r"^image/")
    artifact_ref: ArtifactRef | None = None
    url: str | None = None
    detail: Literal["auto", "low", "high"] = "auto"

    @model_validator(mode="after")
    def require_one_source(self) -> "ImageContentPart":
        if (self.artifact_ref is None) == (self.url is None):
            raise ValueError("image content requires exactly one of artifact_ref or url")
        return self


ContentPart = Annotated[
    Union[TextContentPart, ImageContentPart],
    Field(discriminator="type"),
]

_CONTENT_PARTS_ADAPTER = TypeAdapter(list[ContentPart])


def parse_content_parts(value: object) -> list[ContentPart]:
    """Validate serialized content once through the canonical union."""
    return _CONTENT_PARTS_ADAPTER.validate_python(value)


def merge_content_parts(
    *, text: str | None = None, parts: Iterable[ContentPart] = ()
) -> list[ContentPart]:
    """Build canonical content without maintaining a parallel text field."""
    result = list(parts)
    if text:
        result.insert(0, TextContentPart(text=text))
    return result


def text_from_content_parts(
    parts: Iterable[ContentPart], *, separator: str = "\n"
) -> str:
    """Return the deterministic text projection used by search and tokens."""
    return separator.join(part.text for part in text_parts_from_content_parts(parts))


def text_parts_from_content_parts(
    parts: Iterable[ContentPart],
) -> list[TextContentPart]:
    """Extract typed text parts without repeating union checks at call sites."""
    return [part for part in parts if isinstance(part, TextContentPart)]


def image_parts_from_content_parts(
    parts: Iterable[ContentPart],
) -> list[ImageContentPart]:
    """Extract typed image references without loading their binary payloads."""
    return [part for part in parts if isinstance(part, ImageContentPart)]


def model_content_from_parts(parts: Iterable[ContentPart]) -> object:
    """Serialize canonical parts for a provider-neutral model message."""
    canonical = list(parts)
    if all(isinstance(part, TextContentPart) for part in canonical):
        return text_from_content_parts(canonical)
    serialized = [part.model_dump(mode="json") for part in canonical]
    return serialized if serialized else ""


def select_content_parts(
    parts: Iterable[ContentPart], modalities: frozenset[str]
) -> list[ContentPart]:
    """Select parts supported by a model's declared input modalities."""
    return [
        part
        for part in parts
        if (isinstance(part, TextContentPart) and "text" in modalities)
        or (isinstance(part, ImageContentPart) and "image" in modalities)
    ]
