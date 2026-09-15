"""Framework builtin that submits a validated terminal result."""

from collections.abc import Mapping

from usagi_agent.ports.context import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.tool import ToolAdapterResult, ToolSpec


class StructuredOutputTool(ToolAdapter):
    def __init__(
        self,
        *,
        name: str,
        schema: Mapping[str, object],
        schema_name: str,
        schema_checksum: str,
    ) -> None:
        self._schema_name = schema_name
        self._schema_checksum = schema_checksum
        super().__init__(
            spec=ToolSpec(
                name=name,
                description=(
                    "Submit the final response using the required structured format. "
                    "Call this tool exactly once when all work is complete."
                ),
                parameters=dict(schema),
                requires_approval=False,
                required_scopes=(),
                risk="read",
                max_retries=0,
                timeout_seconds=5,
                max_concurrency=1,
                max_output_bytes=64_000,
                adapter_kind="python",
                execution_env="in_process",
            )
        )

    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> ToolAdapterResult:
        return ToolAdapterResult(
            output={
                "accepted": True,
                "schema_name": self._schema_name,
                "schema_checksum": self._schema_checksum,
                "structured_output": arguments,
            }
        )
