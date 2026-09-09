"""Public conversational response envelope."""
from pydantic import BaseModel

from usagi_agent.types.run import RunOutcome


class SessionMessage(BaseModel):
    session_id: str
    message: str
    run_id: str
    outcome: RunOutcome
