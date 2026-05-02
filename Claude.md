# Infinium — Project Notes

> SRS-aligned implementation status for the Infinium autonomous reasoning agent.

## Repository layout

```
Infinium/
├── AI-Agent/                    backend (FastAPI + ChromaDB + Ollama)
│   ├── backend/
│   │   ├── api/main.py          app factory + health
│   │   ├── api/routes/          one router per concern (modular endpoints)
│   │   │   ├── ingest.py        FR-004, FR-005
│   │   │   ├── remote.py        FR-003 (REST-API ingest, no clone)
│   │   │   ├── access.py        FR-002, FR-003 (pre-flight access check)
│   │   │   ├── query.py         FR-006, FR-007, FR-008
│   │   │   ├── git_history.py   git-aware queries
│   │   │   ├── repos.py         FR-013
│   │   │   ├── dashboard.py     FR-011 (per-user analytics)
│   │   │   ├── ide.py           FR-009 (VSCode extension API)
│   │   │   ├── slack.py         FR-010 (slash command + events)
│   │   │   └── docs.py          FR-012 (auto-doc summariser)
│   │   ├── auth/                FR-001 (GitHub OAuth + JWT)
│   │   ├── ingestion/           clone/load/chunk/embed pipeline
│   │   ├── rag/                 retriever, generator, conversation
│   │   ├── analytics/           query history (powers FR-011 + FR-012)
│   │   └── feedback/            FR-008 store
│   └── chroma_db/               vector index (persistent)
├── vscode-extension/            FR-009 client (TypeScript)
└── HOW_TO_TEST.md               full FR-by-FR test guide
```

## Functional-requirement coverage

| FR | Status | Where |
|---|---|---|
| FR-001 GitHub OAuth | ✅ | `backend/auth/github_oauth.py` |
| FR-002 Repo access validation | ✅ | `backend/api/routes/access.py` |
| FR-003 Public repo support | ✅ | `routes/remote.py` + `routes/access.py` |
| FR-004 On-demand ingestion | ✅ | `routes/ingest.py` + `ingestion/pipeline.py` |
| FR-005 Webhook incremental sync | ✅ | `routes/ingest.py:/api/webhook/github` |
| FR-006 NL query | ✅ | `routes/query.py` + `rag/query_engine.py` |
| FR-007 Synthesised answers | ✅ | `rag/generator.py` (dual-model pipeline) |
| FR-008 Feedback loop | ✅ | `feedback/store.py` + `routes/query.py` |
| FR-009 In-IDE assistance | ✅ | `routes/ide.py` + `vscode-extension/` |
| FR-010 Slack bot | ✅ | `routes/slack.py` |
| FR-011 Personal dashboard | ✅ | `routes/dashboard.py` + `analytics/query_history.py` |
| FR-012 Auto doc summariser | ✅ | `routes/docs.py` |
| FR-013 Repo management | ✅ | `routes/repos.py` |

## Non-functional notes

- **Performance** — async background ingestion; ChromaDB HNSW index;
  configurable `RAG_TOP_K` and `LLM_CONTEXT_WINDOW`; query latency 2–5 s
  (single-model) / 8–15 s (dual-model).
- **Security** — JWT bearer auth; HMAC verification on GitHub webhooks and
  Slack signed requests; per-user GitHub OAuth tokens stored server-side.
- **Reliability** — ingestion status is persisted to disk; embed retries with
  backoff; query history capped at 5 000 entries.
- **Maintainability** — every FR module is its own router file under
  `backend/api/routes/`. New FRs slot in by adding a single router and one
  `app.include_router(...)` line in `api/main.py`.

## Testing

See `HOW_TO_TEST.md` in the repository root for an FR-by-FR walkthrough.
