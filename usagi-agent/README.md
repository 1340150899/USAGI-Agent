# usagi-agent

Business-agnostic Agent engineering framework built on top of LangGraph.

## Current pipeline and memory implementation

Each stage keeps its mandatory orchestration in `PipelineProcessor`. Repeated,
composable work inside a stage is represented by framework-owned rules (for example,
`LongTermMemoryRecallRule`); scenario configuration selects those rules and no longer
imports business-side stage implementations.

`AgentManager` owns agent-to-model bindings, OpenAI-compatible client creation and
invocation, multiple `ModelSpec` registrations, prompts, model limits, pricing metadata
and per-agent/per-model token usage. Model definitions live in one catalog and are
installed by `AgentManagerInitializer`; the model stage only invokes the manager.

All memory behavior is behind `DefaultMemoryManager`: raw events, structured short-term
session state, pre-call token measurement/rolling compaction, explicit and compaction-
triggered long-term extraction, conflict resolution and recall. It depends only on
LangGraph `BaseStore`; V1 uses `JsonFileStore` at `.usagi/memory.json`, so a database
store can replace it without changing pipeline stages.

See `docs/generic-agent-framework.md` for the authoritative design. This package is the
framework Kernel + Capabilities + Ports; it must not contain any business concept
(xiaohongshu, wechat, post, etc.).

## Architecture rules (enforced across this package)

- **Init / Execute separation**: every module exposes an `initializer.py` that only
  constructs + validates at Server Bootstrap, and separate execution files
  (`runtime.py`, `nodes.py`, ...) that only run at Run time. The two never import each
  other's execution logic.
- **Module decoupling**: modules import only stable surfaces — `usagi_agent.types.*`,
  `usagi_agent.ports.*`, `usagi_agent.api.*`. Cross-module wiring happens exclusively in
  `usagi_agent.server.application_container` (the composition root).
- **Static Catalog**: all Specs / Adapters / compiled graphs are built at Bootstrap and
  stored in a read-only `RuntimeBundleCatalog`. A Run only fetches a bundle by
  `scenario_key`; it never parses Specs, creates Adapters or compiles graphs.
- **LangGraph is the only execution engine** (design §3.1): `PipelineCompiler` turns
  Specs into LangGraph `StateGraph` / subgraphs. No second graph runtime.
- **State holds only low-sensitivity routing fields + ArtifactRef** (design §8.2).
