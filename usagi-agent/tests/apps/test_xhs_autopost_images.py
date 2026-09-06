from pathlib import Path

import pytest

from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore, InMemoryArtifactManager, InMemoryArtifactMetadataStore,
)


@pytest.mark.asyncio
async def test_image_attachment_preserves_bytes_and_declares_image_modality(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3] / "apps" / "xhs-autopost"))
    from xhs_autopost.cli import attach_images

    manager = InMemoryArtifactManager(InMemoryArtifactMetadataStore(), InMemoryArtifactBlobStore())
    path = tmp_path / "test.JPG"
    payload = b"\xff\xd8\xff\xe0test-image\xff\xd9"
    path.write_bytes(payload)
    parts = await attach_images(manager, [path], "image-test")
    assert len(parts) == 1
    assert parts[0].media_type == "image/jpeg"
    assert parts[0].detail == "high"
    assert parts[0].artifact_ref is not None
    stream = await manager.get(parts[0].artifact_ref, "run_execution")
    assert b"".join([chunk async for chunk in stream]) == payload
