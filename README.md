# Veyl

[![CI](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml/badge.svg)](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml)

**Permission-aware, multi-tenant RAG.** Veyl answers questions over internal company documents while enforcing tenant isolation and document-level permissions *inside retrieval* — unauthorized chunks never reach the LLM — and audits every retrieval.

Runs entirely on free tiers: local embeddings (bge-small via fastembed) and any OpenAI-compatible free LLM (Groq by default; Gemini or local Ollama also work).

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture & threat model ([spec](docs/superpowers/specs/2026-10-15-veyl-design.md)) | Done |
| 0 | Repo setup: tooling, Docker Compose, CI ([plan](docs/superpowers/plans/2026-10-15-phase-0-repo-setup.md)) | Done |
| 2 | Schema, RLS, auth, RBAC ([plan](docs/superpowers/plans/2026-10-16-phase-2-schema-auth-rbac.md)) | In progress (1/8 tasks) |
| 3 | Ingestion, embeddings, filtered retrieval | Planned |
| 4 | LLM, citations, conversations, audit | Planned |
| 5 | Dashboard | Planned |
| 6 | Security test suite, demo, docs | Planned |

## Quickstart

Requires Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env        # then change the passwords (letters/digits/hyphens)
docker compose up --build
```

| Service | What it does | URL |
|---|---|---|
| `db` | Postgres 16 + pgvector; bootstraps least-privilege roles on first start | `127.0.0.1:5432` |
| `migrate` | One-shot: runs Alembic migrations as `veyl_owner`, then exits | — |
| `api` | FastAPI, connects as `veyl_app` (no superuser, no RLS bypass), runs as non-root | http://127.0.0.1:8000/api/v1/docs |
| `web` | Next.js dashboard; proxies `/api/*` to `api` so the browser sees one origin | http://127.0.0.1:3000 |

All ports bind to `127.0.0.1` only. Reset everything (including the database volume) with `docker compose down -v`.

## Development

### Backend (Python 3.11, [uv](https://docs.astral.sh/uv/))

```bash
cd backend
uv sync                                   # install deps into backend/.venv
uv run pytest                             # tests (integration tests need Docker running)
uv run ruff check . && uv run mypy        # lint + strict type check
```

Integration tests start a throwaway `pgvector/pgvector:pg16` container via [testcontainers](https://testcontainers.com/), bootstrapped by the **same** init script Docker Compose uses ([`docker/postgres/init/01-roles.sh`](docker/postgres/init/01-roles.sh)), then run Alembic migrations as the owner role.

**Database roles (least privilege):** `veyl_owner` owns the schema and runs migrations only; `veyl_app` (used by the API) is not a superuser, cannot bypass row-level security, cannot create tables or temp tables, and cannot execute owner-created functions unless explicitly granted — all verified by tests.

> The init script runs only when the database volume is first created. If you change DB passwords in `.env` later, recreate the volume: `docker compose down -v`.

The API is a FastAPI app factory (`app.main:create_app`). Configuration comes only from environment variables (see [`.env.example`](.env.example)); it refuses to start without `DATABASE_URL`.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/healthz` | Liveness — process is up |
| `GET /api/v1/readyz` | Readiness — DB reachable as the app role; **503 if not** (fails closed) |
| `GET /api/v1/docs` | OpenAPI UI |

**Errors** share one envelope, `{"error": {"code", "message", "trace_id"}}`, and every response carries an `X-Trace-Id` header. Validation errors never echo submitted values; a database outage returns `503 service_unavailable` (fail closed). The database URL is a secret value and never appears in logs or reprs.

### Frontend (Node 24, pnpm 11)

```bash
cd frontend
pnpm install
pnpm dev                                  # http://localhost:3000, proxies /api/* to http://localhost:8000
pnpm test                                 # Vitest + Testing Library
pnpm lint && pnpm typecheck && pnpm build
```

Next.js 16 (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui. The UI never makes authorization decisions; the API is authoritative.

## Repository layout

```
backend/            FastAPI app (app/), Alembic migrations, pytest suite (unit/ + integration/)
frontend/           Next.js dashboard, Vitest
docker/postgres/    DB bootstrap: least-privilege roles + extensions (used by Compose AND tests)
docs/               Spec, plans, decision log, dev log
.github/workflows/  CI: backend, frontend, full-stack Compose smoke test
docker-compose.yml  db → migrate → api → web
```

## Documentation

See [docs/](docs/README.md) — design spec, implementation plans, decision log, dev log.
