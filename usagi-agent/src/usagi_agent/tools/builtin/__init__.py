from usagi_agent.persistence.ports.artifact import ArtifactManager, ArtifactMetadataStore
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.tools.builtin.artifact_reader import ArtifactReaderTool
from usagi_agent.tools.builtin.calculator import CalculatorTool
from usagi_agent.tools.builtin.current_time import CurrentTimeTool
from usagi_agent.tools.builtin.structured_output import StructuredOutputTool


def builtin_tools(
    artifact_manager: ArtifactManager, metadata_store: ArtifactMetadataStore,
    *, specs=None,
) -> tuple[ToolAdapter, ...]:
    from usagi_agent.tools.spec import BUILTIN_TOOL_SPECS
    selected = BUILTIN_TOOL_SPECS if specs is None else specs
    unknown = set(selected) - set(BUILTIN_TOOL_SPECS)
    if unknown:
        raise ValueError(f"unknown builtin tools: {sorted(unknown)}")
    factories = {
        "current_time": lambda spec: CurrentTimeTool(spec=spec),
        "calculator": lambda spec: CalculatorTool(spec=spec),
        "artifact_reader": lambda spec: ArtifactReaderTool(artifact_manager, metadata_store, spec=spec),
    }
    return (
        *[factories[name](spec) for name, spec in selected.items()],
    )


__all__ = [
    "ArtifactReaderTool",
    "CalculatorTool",
    "CurrentTimeTool",
    "StructuredOutputTool",
    "builtin_tools",
]
