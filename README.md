# Veyl

**Permission-aware, multi-tenant RAG.** Veyl answers questions over internal company documents while enforcing tenant isolation and document-level permissions *inside retrieval* — unauthorized chunks never reach the LLM — and audits every retrieval.

Runs entirely on free tiers: local embeddings (bge-small via fastembed) and any OpenAI-compatible free LLM (Groq by default; Gemini or local Ollama also work).

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture & threat model ([spec](docs/superpowers/specs/2026-10-15-veyl-design.md)) | ✅ done |
| 0 | Repo setup: tooling, Docker Compose, CI ([plan](docs/superpowers/plans/2026-10-15-phase-0-repo-setup.md)) | 🚧 in progress — 2/8 tasks |
| 2 | Schema, RLS, auth, RBAC | ⏳ |
| 3 | Ingestion, embeddings, filtered retrieval | ⏳ |
| 4 | LLM, citations, conversations, audit | ⏳ |
| 5 | Dashboard | ⏳ |
| 6 | Security test suite, demo, docs | ⏳ |

## Development

### Backend (Python 3.11, [uv](https://docs.astral.sh/uv/))

```bash
cd backend
uv sync                                   # install deps into backend/.venv
uv run pytest                             # tests (integration tests need Docker running)
uv run ruff check . && uv run mypy        # lint + strict type check
```

The API is a FastAPI app factory (`app.main:create_app`). Configuration comes only from environment variables (see [`.env.example`](.env.example)); it refuses to start without `DATABASE_URL`.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/healthz` | Liveness — process is up |
| `GET /api/v1/docs` | OpenAPI UI |

## Documentation

See [docs/](docs/README.md) — design spec, implementation plans, decision log, dev log.
