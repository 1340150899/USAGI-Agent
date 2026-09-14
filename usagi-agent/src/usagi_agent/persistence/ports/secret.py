"""SecretStore Port.

Secrets are resolved only at the execution boundary and never enter Prompt, State,
Artifact, logs or telemetry.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from usagi_agent.types.refs import SecretRef


@runtime_checkable
class SecretStore(Protocol):
    async def resolve(self, ref: SecretRef) -> bytes:
        """Resolve a SecretRef to its plaintext bytes at the execution boundary."""
        ...

    async def health(self) -> bool: ...
