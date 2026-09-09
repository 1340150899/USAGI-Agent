"""Central default specifications for framework tools; all require approval."""
from usagi_agent.types.tool import ToolSpec, to_model_tool

CURRENT_TIME_SPEC = ToolSpec(
        name="current_time",
        description="Get the current server date and time in UTC.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )

CALCULATOR_SPEC = ToolSpec(
        name="calculator",
        description="Evaluate a basic arithmetic expression.",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )

ARTIFACT_READER_SPEC = ToolSpec(
        name="artifact_reader",
        description="Read a run artifact by its opaque artifact id.",
        parameters={
            "type": "object",
            "properties": {"artifact_id": {"type": "string", "description": "Opaque artifact id"}},
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
    )

BUILTIN_TOOL_SPECS = {s.name: s for s in (CURRENT_TIME_SPEC, CALCULATOR_SPEC, ARTIFACT_READER_SPEC)}
