"""Prompt declaration contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PromptSpec(BaseModel):
    """Immutable, renderable prompt source."""

    model_config = ConfigDict(frozen=True)

    id: str
    template: str

    def render(self, variables: dict[str, object] | None = None) -> str:
        return self.template if not variables else self.template.format_map(variables)
