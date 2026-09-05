# usagi-agent

Business-agnostic Agent engineering framework built on top of LangGraph.

## Multimodal input

Run requests can attach provider-neutral image parts by remote URL or by an
existing Artifact reference. The graph starts with one opaque request Artifact
ID. PreRecall classifies that request; the Model rule then selects only the
modalities supported by its model and resolves image bytes immediately before
the provider call. Provider adapters only translate the prepared content.

Conversation events use `content_parts` as their only content source. Their
`search_text` value is derived from text parts for indexing and token accounting;
it is never maintained as a second model-input field.

```python
from usagi_agent.types.content import ImageContentPart
from usagi_agent.types.run import RunStartRequest

request = RunStartRequest(
    scenario_key="example.research_writer",
    request_idempotency_key="inspect-image-1",
    input=BusinessRequest(query="Inspect the attachment"),
    content_parts=[
        ImageContentPart(
            media_type="image/png",
            url="https://example.com/image.png",
        )
    ],
)
```

## MCP tools

Install the optional MCP dependency with `pip install -e ".[mcp]"`. An
initialized MCP `ClientSession` can then be discovered and registered through
the existing governed ToolManager, so scopes, timeouts, retries and audit
records also apply to remote tools.

```python
from usagi_agent.tools import MCPToolSource

source = MCPToolSource(
    session,
    artifact_manager=runtime.persistence.artifact_manager,
    name_prefix="browser_",
    spec_overrides={
        "screenshot": {"required_scopes": ("browser.read",)},
    },
)
await runtime.tool_manager.load_source(source)
```

MCP image results are stored as Artifacts and returned as typed image content
parts; base64 payloads are not retained in graph state or conversation memory.

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
