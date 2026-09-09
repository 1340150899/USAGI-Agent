"""Read an artifact through the configured artifact manager."""
from __future__ import annotations

from usagi_agent.persistence.ports.artifact import ArtifactManager, ArtifactMetadataStore
from usagi_agent.ports import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.refs import ArtifactRef
from usagi_agent.tools.spec import ARTIFACT_READER_SPEC
from usagi_agent.types.tool import ToolSpec


class ArtifactReaderTool(ToolAdapter):
    spec = ARTIFACT_READER_SPEC

    def __init__(
        self, artifact_manager: ArtifactManager, metadata_store: ArtifactMetadataStore,
        *, spec: ToolSpec | None = None,
    ) -> None:
        self._artifact_manager = artifact_manager
        super().__init__(spec=spec)
        self._metadata_store = metadata_store

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        artifact_id = str(arguments["artifact_id"])
        metadata = await self._metadata_store.get(artifact_id)
        if metadata is None or metadata.tenant_id != context.execution.tenant_id:
            raise PermissionError("artifact is not available to this tenant")
        stream = await self._artifact_manager.get(
            ArtifactRef(artifact_id=artifact_id, content_type="application/octet-stream"),
            "run_execution",
        )
        payload = bytearray()
        async for chunk in stream:
            payload.extend(chunk)
            if len(payload) > 1_000_000:
                raise ValueError("artifact exceeds builtin reader limit")
        return {"artifact_id": artifact_id, "content": bytes(payload).decode("utf-8", errors="replace")}
