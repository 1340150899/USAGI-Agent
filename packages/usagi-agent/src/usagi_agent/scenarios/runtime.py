from __future__ import annotations

from dataclasses import dataclass

from usagi_agent.api.errors import BundleValidationError
from usagi_agent.scenarios.config import ScenarioConfig


@dataclass(frozen=True, slots=True)
class ScenarioRuntime:
    key: str
    config: ScenarioConfig
    compiled_graph: object
    checksum: str


class ScenarioRuntimeRegistry:
    def __init__(self) -> None:
        self._scenarios: dict[str, ScenarioRuntime] = {}

    def register(self, scenario: ScenarioRuntime) -> None:
        if scenario.key in self._scenarios:
            raise BundleValidationError(f"duplicate scenario: {scenario.key}")
        self._scenarios[scenario.key] = scenario

    def get(self, key: str) -> ScenarioRuntime:
        try:
            return self._scenarios[key]
        except KeyError as exc:
            raise BundleValidationError(f"unknown scenario_key: {key}") from exc

    def keys(self) -> tuple[str, ...]:
        return tuple(self._scenarios)

    def __len__(self) -> int:
        return len(self._scenarios)
