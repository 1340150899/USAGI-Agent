# usagi-agent

Business-agnostic Agent engineering framework built on top of LangGraph.

## Tool approval

Every ToolSpec defaults to `requires_approval=True`, including read-only and MCP
tools. Set it explicitly to False at registration to opt out. This is server-side
metadata, never a model argument. Risk controls retries and write safety, not approval.

ToolManager owns the gate. Pipeline nodes translate ToolApprovalRequired into
LangGraph interrupt(); applications collect decisions through any channel and
call Server.resume(). Arguments are stored as an Artifact for authenticated review.
A direct manager without an approval store denies approval-required calls.
SQLite persists approvals, execution contexts, idempotency records, tool results,
artifacts and checkpoints. Configure a stable `resume_hmac_key` for restart recovery.
The application runs one worker per data directory; pending approvals survive
restart, while interrupted external side effects require reconciliation.

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

Install the optional MCP dependency with `pip install -e ".[mcp]"`. MCP
servers are ordinary internal `ToolSource` implementations. The server,
registry, kernel and pipeline layers do not import or special-case MCP. The
application composition root explicitly loads the source before registering
agents and scenarios, so normal tool allow-list validation remains fail-fast.

```python
from pydantic import SecretStr
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import ServiceRuntimeInitializer
from usagi_agent.tools import MCPServerConfig

runtime = ServiceRuntimeInitializer.init(BootstrapSettings())
tools = await runtime.tool_manager.register_mcp(
    MCPServerConfig(
        name="remote",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        headers={"Authorization": SecretStr("Bearer ...")},
        # ca_bundle="/etc/ssl/private/company-ca.pem",  # optional private CA
        enabled_tools=("read_file", "write_file"),
        required_scopes=("filesystem.access",),
        spec_overrides={
            "read_file": {"risk": "read"},
            "write_file": {
                "risk": "high_risk_write",
                "write_safety": "at_most_once_manual",
            },
        },
    )
)
```

Streamable HTTP also supports a private CA bundle, mutual-TLS certificate/key,
an HTTP proxy, HTTP/2 and configurable session termination. For a local MCP
subprocess, use `transport="stdio"` with `command`, `args`, `cwd` and optional
secret environment variables. `ToolManager` owns every successfully loaded
source and closes its HTTP connection or subprocess during runtime shutdown.

The default local tool name is `<server>__<remote_tool>`; use that name in an
agent's `allowed_tools`. Tool names are normalized to the model-safe character
set, paginated discovery is supported, and startup fails on empty discovery or
name collisions. Use `enabled_tools` as an explicit exposure allow-list.

MCP annotations are treated only as untrusted hints. A tool is classified as
read-only only when it explicitly advertises `readOnlyHint=true`; every other
tool defaults to `high_risk_write` plus `at_most_once_manual`. Every tool requires
approval by default, independently of risk. Pin trusted corrections, including
`requires_approval`, in `spec_overrides`.
Configured environment variables and HTTP headers are stored as `SecretStr`
values and are not shown in configuration representations.

Text, structured output, resource links and embedded text are normalized into
the observation. MCP image, audio and embedded binary resource results are
stored as Artifacts, so base64 payloads are not retained in graph state or
conversation memory. Health checks perform an uncached tool-list request, and
runtime shutdown closes HTTP connections or stdio subprocesses.

For embedding or custom connection ownership, the lower-level
`MCPToolSource(session, ...)` API remains available.

For an opt-in live-network smoke test, use Cognition's public, no-auth,
read-only DeepWiki endpoint. The first command only performs discovery; the
second additionally reads the documentation structure of the public MCP Python
SDK repository. The script refuses non-HTTPS URLs and restricts the call mode
to the exact DeepWiki host and tool.

```bash
python scripts/live_mcp_http_smoke.py --url https://mcp.deepwiki.com/mcp
python scripts/live_mcp_http_smoke.py --url https://mcp.deepwiki.com/mcp --deepwiki-read-test
```

## Current pipeline and memory implementation

Each stage keeps its mandatory orchestration in `PipelineProcessor`. Repeated,
composable work inside a stage is represented by framework-owned rules (for example,
`LongTermMemoryRecallRule`); scenario configuration selects those rules and no longer
imports business-side stage implementations.

`AgentManager` owns agent-to-model bindings, OpenAI-compatible client creation and
invocation, multiple `ModelSpec` registrations, prompts, model limits, pricing metadata
and per-agent/per-model token usage. Model definitions live in one catalog and are
installed by `AgentManagerInitializer`; the model stage only invokes the manager.
The exported `DEFAULT_MODEL` is DeepSeek V4 Flash and reads its credential from
`DEEPSEEK_API_KEY`; applications can still select another catalog entry explicitly.

All memory behavior is behind `DefaultMemoryManager`: raw events, structured short-term
session state, pre-call token measurement/rolling compaction, explicit and compaction-
triggered long-term extraction, conflict resolution and recall. It depends only on
LangGraph `BaseStore`; all four memory lifecycles are persisted in the configured
SQLite database.

This package contains the framework Kernel, Capabilities, and Ports; it must not contain any business concept
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
- **LangGraph is the only execution engine**: `PipelineCompiler` turns
  Specs into LangGraph `StateGraph` / subgraphs. No second graph runtime.
- **State holds only low-sensitivity routing fields + ArtifactRef**.
