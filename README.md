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
| 3 | Ingestion, embeddings, filtered retrieval ([plan](docs/superpowers/plans/2026-10-16-phase-3-ingestion-retrieval.md)) | Done |
| 4 | LLM, citations, conversations, audit ([plan](docs/superpowers/plans/2026-10-16-phase-4-llm-citations-audit.md)) | Done |
| 5 | Dashboard ([plan](docs/superpowers/plans/2026-10-17-phase-5-dashboard.md)) | In progress (6/7 tasks) |
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
| `worker` | Ingestion: extract, chunk, embed (model baked into the image), copy ACLs to chunks | internal only |
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

**Answers need an LLM.** Put a free Groq key in `.env` as `LLM_API_KEY` (see [LLM providers](#llm-providers)). No key, or offline? Use the bundled stand-in, which answers from the best-matching source:

```bash
docker compose -f docker-compose.yml -f docker-compose.stub.yml up --build
```

Ask a question (after uploading a document through the API):

```bash
CSRF=$(awk '$6 == "veyl_csrf" { print $7 }' jar.txt)
curl -b jar.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"question": "What flights may employees book?"}' http://127.0.0.1:3000/api/v1/query
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
| `GET /api/v1/users/directory` | Active colleagues `{id, email, role}`, for choosing who to share a document with (manager, admin) |
| `POST /api/v1/users` | Create a user `{email, password, role}` (admin) |
| `PATCH /api/v1/users/{id}` | Change role and/or active status (admin; not yourself) |
| `POST /api/v1/documents` | Upload (multipart: `file`, `title`, `classification`, `allowed_roles`, `allowed_user_ids`); manager or admin, within clearance → 202 |
| `GET /api/v1/documents` | Documents you can read (ACL shown only to those who can manage) |
| `GET /api/v1/documents/{id}` | One readable document (404 otherwise) |
| `PUT /api/v1/documents/{id}/permissions` | Change classification and grants; rewrites every chunk's ACL in the same transaction |
| `DELETE /api/v1/documents/{id}` | Soft delete; removes chunks and the stored file |
| `POST /api/v1/query` | `{question, conversation_id?}` → `{answer, citations[], conversation_id, message_id, trace_id}` |
| `GET /api/v1/conversations` | Your own conversations |
| `GET /api/v1/conversations/{id}` | One of your conversations; answers whose sources you lost are hidden |
| `DELETE /api/v1/conversations/{id}` | Delete one of your conversations |
| `GET /api/v1/audit-events` | Admin only, own tenant: filter by `user_id`, `document_id`, `event_type`, `from`, `to`; keyset pagination via `cursor` |

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

How it talks to the API:

- Every call goes to the same origin (`/api/v1/...`) through a small typed client (`src/lib/api.ts`). The session cookie is HttpOnly and never touched by JavaScript; the client only reads the `veyl_csrf` cookie to echo it in `X-CSRF-Token` on state-changing requests. Nothing is kept in `localStorage`.
- `src/proxy.ts` sends requests without a session cookie to `/login` before rendering. A cookie that is present but no longer valid is caught by the session provider, which loads `/auth/me` and redirects on 401.
- Permission hints from `/auth/me` only decide which controls to show; every action is checked again by the API, and its errors are shown in plain words (`src/lib/messages.ts`).
- Sign-in (`/login`) asks for workspace, email, and password and only continues to same-site paths from `?next=` (never `//host` or absolute URLs). Signed-in pages share a shell with navigation shown from your permission hints and a clearance meter ("Manager can read up to confidential").
- Ask (`/chat`): questions and answers in a conversation list; answers are set in a serif face with superscript source numbers that jump to source cards (title, classification, snippet, link to the document). Markers the server did not verify are dropped rather than shown as broken links.
- Documents (`/documents`): the documents you can read with classification, processing status (refreshed every 3 seconds while anything is queued or processing), and date. Managers and admins upload with a classification picker that only offers levels they are cleared for, choose roles, and share with named colleagues; the uploader and admins edit permissions or delete from the document page.
- People (`/admin/users`, admins): add people with a temporary password, change roles, deactivate or reactivate (never yourself). Audit log (`/admin/audit`, admins): filter by event, person, and date range; newest first with "Show older events"; each row shows the outcome, a short detail, and the trace id.
- Classification levels each have one colour (public sage, internal blue, confidential amber, restricted oxblood), always shown next to the level's name.

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

**Conversations and audit log:** each user's conversations and messages are tenant-scoped like everything else. `audit_events` is append-only: the app role can only insert and read, and a trigger rejects UPDATE, DELETE, and TRUNCATE even from the table owner. Audited: logins (success, and failure when the account exists), user creation and role or status changes, document uploads, permission changes, deletions, ingestion results, tenant setting changes, every question asked, and every read of the audit log. Rows hold ids, decisions, and trace ids, never document text or answers; the question itself is stored only as a keyed hash unless the tenant opts into plaintext.

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

The worker (`python -m app.worker`) claims jobs with `FOR UPDATE SKIP LOCKED` through a narrow database function, so several workers can run safely and a crashed worker's job is reclaimed after 10 minutes. Extraction and embedding happen outside any transaction; the chunks are then written while holding a share lock on the document, copying its ACL at that moment, so a permission change made during ingestion always reaches the new chunks. Unreadable or encrypted files fail immediately with a message the uploader can see; other errors retry up to 3 times with backoff. Extraction runs in a separate process with a time limit, a memory limit (Linux), and a cap on extracted text, so a crafted file (some PDFs take minutes to parse from a few kilobytes) cannot stall ingestion for other tenants; a job that keeps crashing the worker is failed after 3 attempts instead of being retried forever. A document becoming ready invalidates cached search results, so it is searchable immediately.

Tests use a deterministic fake embedder; one test runs the real model (skip it offline with `VEYL_SKIP_MODEL_TESTS=1`).

## Asking questions

`POST /api/v1/query` runs the full pipeline:

1. If a `conversation_id` is given, it must be yours (otherwise 404, audited as `rag.denied`). Earlier turns are re-authorized: an answer built from anything you can no longer read (cited or not) is replaced before it reaches the model, and hidden in the conversation view too.
2. Permission-filtered retrieval (above). If nothing you can read is relevant enough (`RETRIEVAL_MIN_SCORE`, default 0.5), you get "I could not find anything about that..." and **the model is never called**.
3. Otherwise the model sees only your authorized chunks, framed as untrusted sources, plus your re-authorized history.
4. Citations are verified on the server, renumbered `S1..Sn` in order of first use, and returned with document id, title, chunk id, classification, and a short snippet; reopening a conversation returns the same citations.
5. The exchange is stored in your conversation and audited (`rag.query`: chunk and document ids, result count, model, trace id; the question only as a keyed hash unless the tenant opts into plaintext).

Failures fail closed: embedding or database problems give `503 service_unavailable`, an LLM failure gives `503 llm_unavailable`, and an internal authorization disagreement gives `500 authorization_mismatch`. Nothing is stored for a failed question, and the failure is audited.

## LLM providers

Answers come from any OpenAI-compatible chat API, configured in `.env` (never committed). All of these are free without a card:

| Provider | `LLM_BASE_URL` | `LLM_MODEL` | Notes |
|---|---|---|---|
| Groq (default) | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | Free key at console.groq.com |
| Gemini (AI Studio) | `https://generativelanguage.googleapis.com/v1beta/openai/` | e.g. `gemini-2.5-flash` | Free tier may use data for product improvement |
| Ollama (local) | `http://host.docker.internal:11434/v1` | any pulled model | Nothing leaves your machine; `LLM_API_KEY` can be empty |

Only the model's answer text is used; providers that return separate reasoning have it ignored. If the provider fails, times out, or answers with a redirect (refused, so the API key never follows it to another host), the request fails with `503 llm_unavailable`: there is no fallback.

## Prompt injection posture

Retrieved text is treated as untrusted data. Each chunk is wrapped in a labelled `<source>` block, any `<source`/`</source` sequence inside document text or titles is neutralised so it cannot open or close a block, and the system prompt tells the model that instructions inside sources must be ignored. The model has no tools. Most importantly, access control never depends on any of this: retrieval and authorization finish before the model sees anything, so a malicious document can at most distort an answer built from content the user could already read.

Citations are checked on the server: only `[S#]` labels that were actually supplied survive (duplicates collapsed, unknown labels removed from the text), and the citation list is built from the authorized chunks, never from the model's text.

## Retrieval guarantees

Retrieval (`app/retrieval/search.py`) runs in one tenant transaction:

1. Read the tenant's `policy_version`, then look up the cache by `sha256(tenant, user, policy_version, model, normalised query)`.
2. On a miss, embed the query and run the vector search with the SQL read filter **inside** the query, so unauthorized chunks never leave the database. On a hit, the cached **chunk ids** (never content) are re-fetched through the same filter. The cache key also includes `top_k` and the minimum score, so different result shapes never share an entry.
3. Re-check every returned chunk against every rule in Python: tenant, clearance, grant, document ready and not deleted, and matching `acl_version`. Any disagreement aborts the request (`500 authorization_mismatch`) instead of returning data.

There is no fallback path: if embedding or the database fails, the request fails. Permission changes and deletions bump `policy_version`, so every cached result for the tenant stops matching immediately; stale chunks whose `acl_version` lags their document's are invisible even if a rewrite were missed.

Tests prove: tenant A never retrieves tenant B's chunks; members never retrieve admin-only chunks; explicit grants never exceed clearance; permission changes apply to the very next search; a cached result never reaches a less-privileged user, even through a forged cache row; and breaking the SQL filter is caught by the Python re-check.

**Recall note:** small tenants are searched exactly (the planner pre-filters by tenant). In large tenants the HNSW index is approximate; iterative scans keep searching when early candidates are filtered out, but recall under very heavy filtering is best-effort, never at the expense of authorization.

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
