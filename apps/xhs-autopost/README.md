# xhs-autopost

The first business application built on the USAGI framework (design §28, app architecture
doc). This is a **skeleton only** — real adapters (wxauto message source, social-auto-upload
publisher) are not implemented here. The framework core (`usagi-agent`) must not
contain any xiaohongshu / wechat / post business concept (§3).

Planned layout (per app architecture doc §10):

```
src/xhs_autopost/
  domain/        # Message, Snapshot, Draft, ReviewReport models + events + policies
  application/  # pipelines (WorkflowSpec) + services (session/approval/publish use-cases)
  agents/       # boundary, publishability, post_writer, reviewers, revision, memory_curator
  adapters/     # inbound/manual, context_build (chat_retriever, material_selector), plugins
  infrastructure/db/
  interfaces/   # api, worker, review_ui
```

The application depends only on `usagi-agent`'s public API and Plugin/Adapter protocols.
