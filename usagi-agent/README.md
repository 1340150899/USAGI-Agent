# usagi-agent

Business-agnostic Agent engineering framework built on top of LangGraph.

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
