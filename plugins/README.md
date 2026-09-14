# plugins

Pluggable Tool, Retriever, and Adapter packages. The current version constructs adapters
directly through application Bootstrap factories and does not load plugin manifests.
This directory holds installable plugin packages for future use.

Reserved package slots:
- `openai-compatible/` — ModelAdapter
- `postgres/` — durable Store + checkpointer backends
- `wxauto/` — Windows WeChat message source (xiaohongshu app only)
- Xiaohongshu publishing is provided by `apps/xiaohongshu-mcp`, not a plugin.
