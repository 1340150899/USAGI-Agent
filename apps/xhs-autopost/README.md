# xhs-autopost

This application connects a live USAGI agent to
[Algovate/xhs-mcp](https://github.com/Algovate/xhs-mcp) through the framework's
governed MCP adapter. The framework package remains business-agnostic; all
Xiaohongshu-specific configuration lives in this application.

It uses the public registration API directly:

```python
from usagi_agent.tools import MCPServerConfig

tools = await runtime.tool_manager.register_mcp(MCPServerConfig(...))
```

## Setup

Requirements: Python 3.11+, Node.js 18+, and the optional USAGI MCP dependency.

```powershell
python -m pip install -e "usagi-agent[mcp]"
npx.cmd --yes xhs-mcp@0.8.13 browser
npx.cmd --yes xhs-mcp@0.8.13 login --timeout 120
```

On macOS/Linux, use `npx` instead of `npx.cmd`. Login state is managed by
`xhs-mcp`; the USAGI application does not read or store account cookies.

Verify the MCP connection without using model tokens:

```powershell
python apps/xhs-autopost/run.py --list-tools
```

Run the read-only agent after setting `GLM_API_KEY`:

```powershell
$env:GLM_API_KEY = "..."
python apps/xhs-autopost/run.py "搜索关于杭州周末徒步的笔记并总结"
```

By default, only authentication status, feed discovery, search, note detail,
and the current user's note list are exposed. To expose login/logout, comments,
delete, and publish tools, add `--allow-writes`. This authorizes writes in the
test application without interactive approval. Writes are never automatically
retried when their result is uncertain:

```powershell
python apps/xhs-autopost/run.py --allow-writes "发布一篇小红书笔记……"
```

Attach an actual image to the model and let the agent write the copy and call
the publishing tool through the complete pipeline:

```powershell
python apps/xhs-autopost/run.py --allow-writes --coding-plan --image "E:\learning\USAGI-Agent\usagi-agent\scripts\test img\test3.JPG" "根据附件图片，以东营鸟浪为主题丰富文案、添加相关标签并发布一篇图文"
```

`--image` stores the original image as an artifact and sends an image content
part to the model; its absolute path is also supplied for the MCP upload.
`--coding-plan` selects the GLM Responses endpoint. Without it, the application
uses its default GLM Chat endpoint. `--prompt-file` reads a UTF-8 task file.
`--mcp-entry` starts an installed `xhs-mcp.cjs` directly with Node.

For xhs-mcp 0.8.13, add `--mcp-compat --mcp-entry <installed-entry>` when using
the creator center's newer `.note-card` layout. The application wrapper fixes
the note-list, note-item and delete-button selectors in memory before starting
the upstream server. It checks the package version and exact patch locations,
and leaves the installed npm files untouched. The original delete confirmation
logic and the real MCP calls remain in use.

Each agent run writes a JSON report containing the prompt, model, image count,
run ID, token usage, conversation events, model-generated tool arguments and
tool results. `--report` sets an explicit new report path; existing files are
refused to prevent accidentally rerunning the same publication.

An existing HTTP-mode server can be used instead of a managed stdio subprocess:

```powershell
npx.cmd --yes xhs-mcp@0.8.13 mcp --mode http --port 3000
python apps/xhs-autopost/run.py --mcp-url http://127.0.0.1:3000/mcp --list-tools
```

## Direct MCP smoke test

Verify discovery and the actual login status without calling a model:

```powershell
python usagi-agent/scripts/live_xhs_smoke.py
```

If npm registry access is unavailable, pass `--mcp-entry` with the absolute path
to an already installed `xhs-mcp/dist/xhs-mcp.cjs` to run it directly with Node.
To publish one test note without approval prompts:

```powershell
python usagi-agent/scripts/live_xhs_smoke.py --publish --image "E:\learning\USAGI-Agent\usagi-agent\scripts\test img\test3.JPG" --title "东营鸟浪" --content "东营鸟浪" --tags "东营,鸟浪,观鸟,自然摄影"
```

The script checks login, applies the application's tool policy, publishes once,
and fetches the latest five notes. It records the attempt and results in
`.usagi/xhs-live-publish.json`; an existing report prevents another publication.
Use a new `--report` path only for an intentionally separate publication.

## Security note

`xhs-mcp` issue #6 reports SSRF and path traversal in `xhs_publish_content` for
0.8.11, and the report currently does not identify a fixed version. This app
pins 0.8.13 for reproducibility but keeps publishing disabled by default. Before
enabling writes, audit the installed release, use trusted local media paths,
avoid untrusted URLs, and run the MCP process with least filesystem and network
privilege.
