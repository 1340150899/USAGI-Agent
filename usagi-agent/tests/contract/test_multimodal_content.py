from __future__ import annotations

import base64

import pytest
from pydantic import BaseModel, ValidationError

from usagi_agent.agents.model_adapters import OpenAICompatibleModelAdapter
from usagi_agent.models import GLM_5_3_FLASH_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.pipelines.processors.context_build import ContextBuildProcessor
from usagi_agent.pipelines.rules.model_execution import ModelExecutionRule
from usagi_agent.types.content import (
    ImageContentPart,
    TextContentPart,
    image_parts_from_content_parts,
    text_from_content_parts,
)
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelSpec
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef
from usagi_agent.types.run import RunStartRequest
from usagi_agent.memory.types import RawEvent


def _artifacts() -> InMemoryArtifactManager:
    return InMemoryArtifactManager(
        InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore()
    )


def test_image_part_requires_exactly_one_non_binary_source():
    with pytest.raises(ValidationError, match="exactly one"):
        ImageContentPart(media_type="image/png")
    with pytest.raises(ValidationError, match="exactly one"):
        ImageContentPart(
            media_type="image/png",
            url="https://example.test/image.png",
            artifact_ref=ArtifactRef(
                artifact_id="a", content_type="image/png"
            ),
        )


def test_context_build_preserves_provider_neutral_parts():
    event = RawEvent(
        event_id="e1",
        session_id="s1",
        role="user",
        content_parts=[
            TextContentPart(text="fallback"),
            TextContentPart(text="inspect"),
            ImageContentPart(
                media_type="image/png", url="https://example.test/image.png"
            ),
        ],
    )
    message = ContextBuildProcessor._event_to_message(event)
    assert message["content"] == [
        {"type": "text", "text": "fallback"},
        {"type": "text", "text": "inspect"},
        {
            "type": "image",
            "media_type": "image/png",
            "artifact_ref": None,
            "url": "https://example.test/image.png",
            "detail": "auto",
        },
    ]


def test_raw_event_has_one_content_source_and_reads_legacy_records():
    event = RawEvent.model_validate(
        {
            "event_id": "legacy",
            "session_id": "session",
            "role": "user",
            "content": "legacy text",
            "content_parts": [
                {
                    "type": "image",
                    "media_type": "image/png",
                    "url": "https://example.test/legacy.png",
                }
            ],
        }
    )

    assert "content" not in RawEvent.model_fields
    assert event.search_text == "legacy text"
    assert [part.type for part in event.content_parts] == ["text", "image"]
    assert text_from_content_parts(event.content_parts) == "legacy text"
    assert len(image_parts_from_content_parts(event.content_parts)) == 1
    assert "content" not in event.model_dump()


@pytest.mark.asyncio
async def test_model_rule_resolves_image_before_provider_adapter():
    artifacts = _artifacts()

    async def chunks():
        yield b"image-bytes"

    ref = await artifacts.put(
        operation_id="put-image",
        owner=ArtifactOwner(tenant_id="tenant", erasure_scope_id="run"),
        lineage=[],
        payload=chunks(),
        purpose="run_execution",
    )
    ref = ref.model_copy(update={"content_type": "image/png"})
    request = ModelRequest(
        messages_ref=ArtifactRef(
            artifact_id="messages", content_type="application/json"
        ),
        context_pack_ref=ArtifactRef(
            artifact_id="context", content_type="application/json"
        ),
        max_output_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    ImageContentPart(
                        media_type="image/png", artifact_ref=ref, detail="high"
                    ).model_dump(mode="json"),
                ],
            }
        ],
    )
    prepared = await ModelExecutionRule._prepare_model_input(
        request, frozenset({"text", "image"}), artifacts
    )
    adapter = OpenAICompatibleModelAdapter(
        ModelSpec(
            id="test",
            provider_model="vision",
            input_modalities=frozenset({"text", "image"}),
        )
    )
    messages = await adapter._messages_for_openai(prepared.messages)
    expected = base64.b64encode(b"image-bytes").decode("ascii")
    assert messages[0]["content"] == [
        {"type": "text", "text": "inspect"},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{expected}",
                "detail": "high",
            },
        },
    ]


@pytest.mark.asyncio
async def test_model_rule_removes_images_for_text_only_model():
    request = ModelRequest(
        messages_ref=ArtifactRef(
            artifact_id="messages", content_type="application/json"
        ),
        context_pack_ref=ArtifactRef(
            artifact_id="context", content_type="application/json"
        ),
        max_output_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    ImageContentPart(
                        media_type="image/png",
                        url="https://example.test/image.png",
                    ).model_dump(mode="json"),
                ],
            }
        ],
    )
    prepared = await ModelExecutionRule._prepare_model_input(
        request, frozenset({"text"}), _artifacts()
    )
    assert prepared.messages[0]["content"] == [
        {"type": "text", "text": "inspect"}
    ]


class _Request(BaseModel):
    query: str


class _CaptureModel:
    adapter_ref = "test.capture_multimodal"

    def __init__(self) -> None:
        self.request: ModelRequest | None = None

    async def generate(self, request, ctx):
        self.request = request
        return ModelResponse(
            content='{"answer":"seen","context_update":{}}'
        )

    async def health(self):
        return "healthy"


@pytest.mark.asyncio
async def test_run_ingress_delivers_image_part_to_model_context(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="scripted",
            sqlite_path=str(tmp_path / "runtime.db"),
        )
    )
    server = Server(runtime)
    capture = _CaptureModel()
    runtime.agent_manager.set_model_adapter(capture)
    server.create_agent(
        id="research_writer",
        input_schema="usagi.agent_request@1.0.0",
        model=GLM_5_3_FLASH_MODEL,
    )
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    handle = await server.create_session(
        RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key="multimodal-ingress",
            input=_Request(query="inspect the attachment"),
            content_parts=[
                ImageContentPart(
                    media_type="image/png",
                    url="https://example.test/image.png",
                )
            ],
        )
    )
    outcome = await server.get_run(handle.run_id)
    assert outcome.kind == "completed"
    assert capture.request is not None
    user = next(
        message
        for message in capture.request.messages
        if message.get("role") == "user"
    )
    assert isinstance(user["content"], list)
    assert "inspect the attachment" in user["content"][0]["text"]
    assert user["content"][-1]["type"] == "image"
    assert user["content"][-1]["url"] == "https://example.test/image.png"
    await server.shutdown()
