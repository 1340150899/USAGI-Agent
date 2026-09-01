"""InMemory dev implementations of all Store Ports (design §24.1, M0A spike).

Single-process, volatile. Correctness of CAS / lease / idempotency semantics is
preserved so the same Runtime API contract holds; durability is NOT promised here
(§30.1: InMemory checkpoint carries no recovery promise). SQLite backends add durability.
"""
from usagi_agent.persistence.inmemory.run_lifecycle import (
    InMemoryExecutionContextStore,
    InMemoryInterruptCredentialStore,
    InMemoryResumeAttemptStore,
    InMemoryRunControlStore,
    InMemoryRunStartRequestStore,
)
from usagi_agent.persistence.inmemory.accounting import InMemoryAuditStore, InMemoryUsageLedger
from usagi_agent.persistence.inmemory.stores import (
    InMemoryApprovalStore,
    InMemoryErasureControlStore,
    InMemoryKeyDestructionStore,
    InMemoryLineageIndex,
    InMemoryMemoryStore,
    InMemoryModelInvocationStore,
    InMemoryOutboxStore,
    InMemorySecretStore,
    InMemoryToolExecutionStore,
    InMemoryVectorStore,
)
from usagi_agent.persistence.inmemory.artifact import (
    InMemoryArtifactBlobStore,
    InMemoryArtifactManager,
    InMemoryArtifactMetadataStore,
)
from usagi_agent.persistence.inmemory.checkpointer import InMemoryFencedCheckpointer

__all__ = [
    "InMemoryExecutionContextStore",
    "InMemoryInterruptCredentialStore",
    "InMemoryResumeAttemptStore",
    "InMemoryRunControlStore",
    "InMemoryRunStartRequestStore",
    "InMemoryAuditStore",
    "InMemoryUsageLedger",
    "InMemoryApprovalStore",
    "InMemoryErasureControlStore",
    "InMemoryKeyDestructionStore",
    "InMemoryLineageIndex",
    "InMemoryMemoryStore",
    "InMemoryModelInvocationStore",
    "InMemoryOutboxStore",
    "InMemorySecretStore",
    "InMemoryToolExecutionStore",
    "InMemoryVectorStore",
    "InMemoryArtifactBlobStore",
    "InMemoryArtifactManager",
    "InMemoryArtifactMetadataStore",
    "InMemoryFencedCheckpointer",
]
