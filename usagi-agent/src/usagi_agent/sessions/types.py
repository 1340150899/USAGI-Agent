"""Public conversational response envelope; not a persisted Session record."""
from pydantic import BaseModel, Field

from usagi_agent.types.run import RunOutcome


class SessionMessage(BaseModel):
    session_id: str
    message: str
    run_id: str
    outcome: RunOutcome
    structured_output: dict[str, object] | None = Field(
        default=None,
        description="Validated output returned by this run; never Session metadata.",
    )
