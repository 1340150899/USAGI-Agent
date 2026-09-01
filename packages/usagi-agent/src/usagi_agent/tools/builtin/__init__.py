from usagi_agent.persistence.ports.artifact import ArtifactManager, ArtifactMetadataStore
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.tools.builtin.artifact_reader import ArtifactReaderTool
from usagi_agent.tools.builtin.calculator import CalculatorTool
from usagi_agent.tools.builtin.current_time import CurrentTimeTool


def builtin_tools(
    artifact_manager: ArtifactManager, metadata_store: ArtifactMetadataStore
) -> tuple[ToolAdapter, ...]:
    return (
        CurrentTimeTool(),
        CalculatorTool(),
        ArtifactReaderTool(artifact_manager, metadata_store),
    )


__all__ = ["ArtifactReaderTool", "CalculatorTool", "CurrentTimeTool", "builtin_tools"]
