# plugins

Pluggable Tool / Retriever / Adapter packages (design §26). v1 does not implement the
plugin manifest loader — adapters are constructed directly by application Bootstrap
factories (§26.1). This directory holds installable plugin packages for future use.

Reserved slots per the design:
- `openai-compatible/` — ModelAdapter
- `postgres/` — durable Store + checkpointer backends
- `wxauto/` — Windows WeChat message source (xiaohongshu app only)
- Xiaohongshu publishing is provided by `apps/xiaohongshu-mcp`, not a plugin.
