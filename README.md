# Veyl

[![CI](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml/badge.svg)](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml)

**Permission-aware, multi-tenant RAG.** Veyl answers questions over internal company documents while enforcing tenant isolation and document-level permissions *inside retrieval* — unauthorized chunks never reach the LLM — and audits every retrieval.

Runs entirely on free tiers: local embeddings (bge-small via fastembed) and any OpenAI-compatible free LLM (Groq by default; Gemini or local Ollama also work).

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture & threat model ([spec](docs/superpowers/specs/2026-10-15-veyl-design.md)) | Done |
| 0 | Repo setup: tooling, Docker Compose, CI ([plan](docs/superpowers/plans/2026-10-15-phase-0-repo-setup.md)) | Done |
| 2 | Schema, RLS, auth, RBAC ([plan](docs/superpowers/plans/2026-10-16-phase-2-schema-auth-rbac.md)) | Done |
| 3 | Ingestion, embeddings, filtered retrieval ([plan](docs/superpowers/plans/2026-10-16-phase-3-ingestion-retrieval.md)) | In progress (5/8 tasks) |
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
| `web` | Next.js dashboard; proxies `/api/*` to `api` so the browser sees one origin | internal only |
| `edge` | Caddy reverse proxy, the public entry point; overwrites `X-Forwarded-For` so client IPs cannot be forged | http://127.0.0.1:3000 |

### Provisioning a tenant

Tenants are created by an operator with the database owner role, never through the API. The admin password is read from an environment variable so it never appears in the process list or shell history as an argument:

```bash
docker compose run --rm -e VEYL_ADMIN_PASSWORD='choose-a-long-password' migrate   python -m app.cli provision-tenant --slug acme --name "Acme Corp" --admin-email alice@acme.test
```

Then log in (through the web proxy, as the browser would):

```bash
curl -c jar.txt -H 'Content-Type: application/json'   -d '{"tenant_slug":"acme","email":"alice@acme.test","password":"choose-a-long-password"}'   http://127.0.0.1:3000/api/v1/auth/login
curl -b jar.txt http://127.0.0.1:3000/api/v1/auth/me
```

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
| `POST /api/v1/auth/login` | `{tenant_slug, email, password}` → sets session + CSRF cookies, returns identity |
| `POST /api/v1/auth/logout` | Revokes the session (needs `X-CSRF-Token`) |
| `GET /api/v1/auth/me` | Current user, tenant, and permission hints |
| `GET /api/v1/tenant` | Current tenant |
| `PATCH /api/v1/tenant` | Rename tenant (admin) |
| `GET /api/v1/users` | List users in your tenant (admin) |
| `POST /api/v1/users` | Create a user `{email, password, role}` (admin) |
| `PATCH /api/v1/users/{id}` | Change role and/or active status (admin; not yourself) |
| `POST /api/v1/documents` | Upload (multipart: `file`, `title`, `classification`, `allowed_roles`, `allowed_user_ids`); manager or admin, within clearance → 202 |
| `GET /api/v1/documents` | Documents you can read (ACL shown only to those who can manage) |
| `GET /api/v1/documents/{id}` | One readable document (404 otherwise) |
| `PUT /api/v1/documents/{id}/permissions` | Change classification and grants; rewrites every chunk's ACL in the same transaction |
| `DELETE /api/v1/documents/{id}` | Soft delete; removes chunks and the stored file |

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

## Authorization model

All permission decisions live in one package, [`backend/app/authz`](backend/app/authz). Everything else calls into it.

| Role | Clearance (highest classification readable) | Can also |
|---|---|---|
| member | internal | query and read documents shared with them |
| manager | confidential | upload documents; manage the ones they uploaded |
| admin | restricted | manage users, tenant settings, all documents, audit log |

Classifications, lowest to highest: `public`, `internal`, `confidential`, `restricted`.

A document chunk is readable only if it belongs to the caller's tenant, its classification is within the caller's clearance, **and** the caller is an admin, has a role in the chunk's `allowed_roles`, or is listed in its `allowed_user_ids`. An explicit per-user grant never lifts the clearance ceiling. Property-based tests (Hypothesis) check these invariants over random inputs.

The same rule exists twice: as SQL predicates applied inside every document and vector query (`app/authz/sql.py`), and in Python (`can_read_chunk`) to re-check every row before it is used. A test proves the two agree for every combination of 3 tenants, 3 users, 3 roles, 4 classifications, 8 role grants, and 4 user grants; breaking either side makes it fail.

**Tenant isolation in the database:** every table has row-level security enabled *and forced*. The API's `veyl_app` role only sees rows whose `tenant_id` matches the transaction-local setting `app.tenant_id`, which the API sets from the server-side session; with no setting it sees nothing. Writes into another tenant are rejected by the policy's `WITH CHECK`. The owner role (migrations and two narrow lookup functions) has its own explicit policy. `veyl_app` gets column-level grants only: it cannot create tenants, delete users, or rewrite a user's tenant, email, or password hash. A test fails if any future table lacks forced RLS.

**Sessions:** login sets `veyl_session` (opaque token, HttpOnly, SameSite=Lax, Secure in production) and `veyl_csrf` (readable by the frontend). Every state-changing request must echo the CSRF cookie in an `X-CSRF-Token` header. Each request re-resolves the session in the database, so revocation, deactivation, and role changes take effect immediately. Login failures return one generic error whatever the cause; repeated failures lock the account temporarily.

**User management guards:** admins cannot change their own role or status, a tenant always keeps at least one active admin (enforced under a row lock, so concurrent demotions cannot race past it), deactivating a user revokes their sessions, and every role or status change bumps the tenant's `policy_version` (used later to invalidate cached retrieval results). Identifiers from another tenant return 404.

**Documents and chunks:** each document carries its access-control list (`classification`, `allowed_roles`, `allowed_user_ids`) and an `acl_version`. Every chunk stores a copy of that ACL next to its 384-dimensional embedding (pgvector, HNSW index), so permission filtering happens inside the vector query. Composite foreign keys make it impossible for a chunk, job, or cache row to point at another tenant's document or user, and unknown role names are rejected by a database check.

**Credentials:** passwords are hashed with argon2id (minimum 12 characters); unknown accounts still pay the hashing cost so timing does not reveal which accounts exist. Session tokens are random 256-bit values stored only as SHA-256 digests. Failed logins are rate-limited per account and, more loosely, per client IP (the client IP comes from the `edge` proxy and cannot be forged; a smoke test proves it). At most four password checks run at once; excess attempts get 429 instead of tying up the server.

## Uploads

- **Types:** PDF, TXT, and Markdown only. The extension must match the content (a PDF must start with `%PDF-`; text must be valid UTF-8 with no NUL bytes).
- **Size:** `MAX_UPLOAD_MB` (default 10). The limit is enforced while the body streams in, without trusting `Content-Length`, so an oversized upload is refused before it is buffered.
- **Storage:** originals live under `UPLOAD_DIR/<tenant_id>/<document_id>`, keys generated by the server. The uploaded filename is kept only as display metadata, reduced to a safe basename.

## Ingestion

Uploaded files are processed by a background worker:

1. **Extract** text. PDFs via pypdf (encrypted PDFs and files over the page cap are rejected), TXT and Markdown as UTF-8 (NUL bytes rejected).
2. **Chunk** it: paragraphs, then sentences, then hard cuts, about 800 characters with about 120 characters of overlap; a Markdown heading always starts a new chunk with its section.
3. **Embed** each chunk locally with `BAAI/bge-small-en-v1.5` (384 dimensions, ONNX via fastembed). No API key, no network once the model is cached, $0.

Tests use a deterministic fake embedder; one test runs the real model (skip it offline with `VEYL_SKIP_MODEL_TESTS=1`).

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
