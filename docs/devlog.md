# Dev log

Newest entries first. Each entry: what was done, what's next, anything blocked.

## 2026-10-16 — Phase 3, Task 1: documents schema and job claiming

**Done** — migration 0003: `classification`/`doc_status`/`job_status` enums; `documents` (ACL, `acl_version`, status, soft delete), `document_chunks` (`vector(384)`, HNSW cosine index, ACL copy, GIN on role/user arrays), `ingestion_jobs`, `retrieval_cache`; all with forced RLS, owner policy, column-level grants, composite tenant FKs. `ingestion_claim_job(interval)` SECURITY DEFINER with `FOR UPDATE SKIP LOCKED` and stale-job reclaim. Membership lock is now `FOR NO KEY UPDATE` (Phase 2 review minor 2; mutation check: `FOR UPDATE` makes the new test fail). New settings for uploads, embeddings, retrieval, worker. Deps: pgvector, fastembed, pypdf, python-multipart. 149 tests green.
**Next** — SQL read filters with an equivalence proof (Task 2).

## 2026-10-16 — Phase 3 plan written

**Done** — [Phase 3 plan](superpowers/plans/2026-10-16-phase-3-ingestion-retrieval.md), 8 tasks: migration 0003 (documents, chunks with `vector(384)` and denormalised ACL, ingestion jobs, retrieval cache, job-claim function); SQL read filters proven equal to the Python policy over an exhaustive ACL universe; local bge-small embeddings, PDF/TXT/MD extraction, chunker; server-keyed storage, upload validation, streamed body limit; documents API; ingestion worker with a race-safe ACL copy; permission-filtered retrieval with an ids-only cache and a Python re-check; containers, end-to-end ingestion smoke in CI, docs.
**Folded in from the Phase 2 review** — membership lock becomes `FOR NO KEY UPDATE`; retrieval reads `policy_version` in the same transaction as the search; pgvector iterative scans for small-tenant recall.
**Next** — execute Task 1.

## 2026-10-16 — Phase 2 review: fixes and deferred items

**Review** — independent whole-phase review: no Critical, 2 Important, 8 Minor. Data isolation and authorization model judged sound.

**Fixed (Important)**
- *Per-IP login limit bypassable with a forged `X-Forwarded-For`.* Next.js only sets the header when missing, so a client value reached uvicorn (which trusts forwarded headers). Added `edge` (Caddy) as the sole public entry point; it overwrites the header with the real peer. `docker/smoke/login-ip-limit.sh` (21 failed logins, each with a different forged IP) returned 401 before the fix and 429 after; it now runs in CI. Also capped concurrent password verifications (`test_login_sheds_load_when_password_hashing_is_saturated` RED to GREEN) so a flood cannot exhaust memory or threads. D-020 superseded by D-022, D-023.
- *Rate limiter kept every key that ever failed.* The Task 3 fix only covered keys that were looked up again. Added a once-per-window sweep (`test_limiter_sweeps_keys_whose_failures_have_expired` RED to GREEN: 1001 keys before, at most 1 after).

**Deferred (Minor), to address in Phase 3 or later**
1. Caller's own admin status is checked before the tenant lock (narrow window after demotion or deactivation); re-check the caller inside the transaction.
2. `FOR UPDATE` on the tenant row conflicts with foreign-key key-share locks; use `FOR NO KEY UPDATE` before Phase 3 adds document inserts.
3. Session-expiry test tolerance (5 s) cannot distinguish the DB clock from the app clock; assert exactly 3600.
4. No test for a non-ASCII CSRF cookie (the header case is tested).
5. NUL bytes in login `tenant_slug` or `email` cause a 500 and are not counted by the limiter; reject them with 422.
6. Rate-limit check and record are not atomic (a concurrent burst can exceed the limit once); the hashing cap now bounds the damage.
7. Login does not revoke an already-presented session; logout with a dead session leaves cookies; no CSRF-cookie refresh path.
8. Provisioning reports any integrity error as "slug already exists"; CLI email is not format-validated.

**Phase 3 notes from review** — RLS filters rows after the HNSW scan, so top-k recall drops with many tenants (use pgvector iterative scans); read `policy_version` in the same transaction as retrieval.

## 2026-10-16 — Phase 2 complete: schema, RLS, auth, RBAC

**Summary** — tenants/users/sessions behind forced row-level security with column-level grants; two SECURITY DEFINER lookups (login, session) as the only cross-tenant reads; central `authz` policy with property tests; argon2id + hashed session tokens + per-account/per-IP lockout; `/auth/*`, `/tenant`, `/users` with CSRF double-submit, generic login errors, no self-escalation, one-admin invariant under a row lock; operator CLI for tenant provisioning. 138 backend tests (unit, integration, security). Compose API now trusts forwarded headers behind the local proxy; CI smoke test provisions a tenant and logs in through the Next.js proxy. Verified locally end to end, including CSRF-protected logout through the proxy. Decisions D-015 to D-021; spec sections 5.1, 7, 11 updated.

**Still deferred from the Phase 0 review** — readyz statement timeout; LOG_LEVEL not applied; `.env*` in `.dockerignore`; Compose cap_drop/no-new-privileges/read_only; PUBLIC CONNECT on the `postgres` DB; CI actions SHA pinning and timeouts; automated "api has no owner creds" check; prettier and pytest-cov.

**Next** — whole-phase review, then the Phase 3 plan (ingestion, embeddings, filtered retrieval).

## 2026-10-16 — Phase 2, Task 7: tenant and user management

**Done** — `/tenant` (GET, PATCH name) and `/users` (GET, POST, PATCH role/is_active), admin-only via `authorize`, CSRF on writes, `extra=forbid` bodies. Guards: no self role/status change; at least one active admin kept under a tenant row lock (mutation check: removing the lock makes the concurrency test fail 3/3); `policy_version` bump on every change; deactivation revokes sessions; cross-tenant ids 404. Security tests: non-admins blocked (T10), demotion effective next request, forged tenant ids in body/header/query ignored or rejected (T4), cross-tenant modification 404 (T1). 138 tests green.
**Next** — Compose proxy headers, end-to-end login smoke in CI, docs close-out, final review (Task 8).

## 2026-10-16 — Phase 2, Task 6: authentication API

**Done** — `app/auth/service.py` (login with normalised slug/email, dummy-hash timing equaliser, per-account + per-IP lockout, session expiry from the DB clock; session resolution via `auth_resolve_session`; logout revokes), `app/auth/deps.py` (`CurrentUser` from the cookie only; CSRF double-submit compared as bytes so non-ASCII headers give 403, not 500), `app/auth/router.py` (`/auth/login`, `/auth/logout`, `/auth/me`). Security tests: cookie flags (Secure in production), token stored only as SHA-256, indistinguishable login failures, CSRF required, lockout, oversized password rejected before hashing, no CORS, missing/garbage/expired/revoked sessions and deactivated users denied, DB outage gives 503. 121 tests green.
**Next** — tenant and user management API (Task 7).

## 2026-10-16 — Phase 2, Task 5: auth lookups and provisioning

**Done** — migration 0002: `auth_resolve_login(slug, email)` and `auth_resolve_session(token_hash)`, both SECURITY DEFINER with `search_path = public, pg_temp`, EXECUTE revoked from PUBLIC and granted only to `veyl_app`. Session lookup returns only unexpired, unrevoked sessions of active users. `app/provisioning.py` + `python -m app.cli provision-tenant` (owner URL; password from `VEYL_ADMIN_PASSWORD`, never argv). Guard tests: every definer function pins search_path; the app role can execute exactly the two intended definer functions. 99 tests green.
**Next** — authentication API (Task 6).

## 2026-10-16 — Phase 2, Task 4: schema and row-level security

**Done** — migration 0001: `user_role` enum, `app_current_tenant()` (NULLIF guard), `tenants`/`users`/`sessions` with CHECK constraints and composite FK pinning sessions to same-tenant users; ENABLE + FORCE RLS with `*_tenant_isolation` (veyl_app) and `*_owner_all` (veyl_owner) policies; column-level grants. `app/db/schema.py` (Core tables), `app/db/tenancy.py` (`tenant_transaction`, UUID-only). Tests: schema matches migration, every table has forced RLS, no-context sees nothing, own-tenant only, cross-tenant insert refused, context does not leak across pooled transactions, malformed setting fails closed, privilege limits. 84 tests green.
**Next** — definer lookup functions and operator provisioning (Task 5).

## 2026-10-16 — Phase 2, Task 3: security primitives

**Done** — `app/auth/passwords.py` (argon2id, dummy verify for unknown users, 12..1024 char bounds), `app/auth/tokens.py` (256-bit URL-safe tokens, SHA-256 digests), `app/auth/ratelimit.py` (sliding-window limiter; per-account and 4x looser per-IP). Extra fix beyond the plan: the limiter no longer retains a key for every probe (unbounded memory under account enumeration). 70 tests green.
**Next** — schema migration with forced RLS (Task 4).

## 2026-10-16 — Phase 2, Task 2: authorization policy core

**Done** — `app/authz`: `Role`, `Classification`, clearance map, `AuthContext`, `Action`, `is_allowed`/`authorize` (deny by default; document actions require a same-tenant resource within clearance; managers manage only their own uploads), `permissions(role)` UI hints, and the Python chunk read rule `can_read_chunk`. Hypothesis (added; Phase 0 review minor #10) property tests: readable implies same tenant + within clearance + a grant; document actions never cross tenant or exceed clearance. 59 tests green.
**Next** — password/token/rate-limit primitives (Task 3).

## 2026-10-16 — Phase 2, Task 1: errors, trace ids, settings

**Done** — `app/errors.py`: `AppError` hierarchy (401/403/404/409/429/503) and one error envelope with a server-generated trace id (also in `X-Trace-Id`). Validation errors drop submitted values; `OperationalError` maps to 503; unknown exceptions return a generic 500. `Settings.database_url` is now `SecretStr` (Phase 0 review minor #3); added session TTL, login lockout settings, and `cookie_secure` (production only). 27 tests green.
**Next** — authz policy core (Task 2).

## 2026-10-16 — Phase 2 plan written

**Done** — [Phase 2 plan](superpowers/plans/2026-10-16-phase-2-schema-auth-rbac.md), 8 tasks: error envelope + trace ids + SecretStr settings; pure `authz` policy with Hypothesis properties; argon2id/token/rate-limit primitives; migration 0001 (tenants, users, sessions, forced RLS, column-level grants); definer lookups + operator provisioning CLI; session auth API with CSRF double-submit and lockout; tenant/user management with escalation guards; Compose proxy headers, end-to-end login smoke test in CI, docs.
**Spec refinements found while planning** — RLS predicate must use `NULLIF(current_setting(...), '')` (a used transaction-local setting reads back as `''`); FORCE RLS needs an explicit owner policy so SECURITY DEFINER lookups can read across tenants.
**Next** — user reviews the plan.

## 2026-10-16 — No-emoji rule

**Done** — new project rule from the user: no emojis anywhere. Replaced status icons in README and the Phase 0 plan with words, and check marks in the spec's permission matrix with Yes/No. Added `backend/tests/unit/test_no_emojis.py`, which scans every tracked file (lockfiles and binaries excluded) and fails CI on any emoji, pictograph, or dingbat; verified it catches a planted emoji. 16 tests green.
**Next** — Phase 2 plan: schema, RLS, auth, RBAC.

## 2026-10-15 — Phase 0 review: fixes and deferred items

**Review** — independent whole-phase review: no Critical, 2 Important, 10 Minor.
**Fixed (Important)**
- Default-privilege REVOKE of function EXECUTE was scoped `IN SCHEMA public`, which cannot remove PostgreSQL's built-in PUBLIC EXECUTE — owner functions (future SECURITY DEFINER helpers) were callable by `veyl_app`. Now global. Test `test_app_role_cannot_execute_owner_functions_by_default` RED→GREEN.
- `veyl_app` had `TEMPORARY` on the database (pg_temp shadowing risk for definer functions). Now `CONNECT` only. Test `test_app_role_cannot_create_temp_tables` RED→GREEN. Spec §5.1 now requires `search_path = public, pg_temp` on every definer function.

**Deferred (Minor) — to pick up in later phases**
1. `/readyz` has no `statement_timeout`; a DB that accepts then stalls could hang it (compose healthcheck timeout bounds it).
2. `LOG_LEVEL` is configured but not applied.
3. `database_url` (contains password) should be `SecretStr` before secrets like `LLM_API_KEY` arrive (Phase 2/4).
4. Add `.env*` to both `.dockerignore` files.
5. Compose hardening: `cap_drop: [ALL]`, `no-new-privileges`, `read_only`.
6. Revoke PUBLIC CONNECT on the `postgres` maintenance DB.
7. Pin CI actions by SHA; add `timeout-minutes`.
8. Automate the "api has no owner credentials" check in compose-smoke.
9. (done) README note: changing DB passwords needs `docker compose down -v`.
10. Spec §12 lists prettier, pytest-cov, hypothesis — not yet added (hypothesis lands with the authz equivalence test in Phase 2).

**Phase 2 notes from review** — uvicorn `--forwarded-allow-ips` for per-IP rate limiting behind the Next proxy; explicit per-table GRANTs to `veyl_app` (fail-closed) with tests; disable OpenAPI docs when `ENVIRONMENT=production`; verify provenance of the `cn` npm package pulled in by shadcn.

## 2026-10-15 — Phase 0 complete: repo setup

**Summary** — monorepo in place: `backend/` (FastAPI factory, env-only settings, fail-closed `/readyz`, Alembic on the owner role, 13 tests incl. real-Postgres role checks), `frontend/` (Next.js 16 + Tailwind + shadcn + Vitest, `/api` proxy), `docker/postgres/init` least-privilege bootstrap shared by Compose and tests, `docker-compose.yml` (db → migrate → api → web, non-root, localhost-only ports), GitHub Actions CI green on all three jobs. Decisions D-012 (worker deferred to Phase 3) and D-013 (Next 16 defaults + typegen) recorded.
**Next** — whole-phase code review, then the Phase 2 plan: schema, RLS, auth, RBAC.

## 2026-10-15 — Phase 0, Task 7: GitHub Actions CI

**Done** — `.github/workflows/ci.yml`: `backend` (uv sync --frozen, ruff check + format, mypy strict, pytest incl. testcontainers integration tests), `frontend` (pnpm install --frozen-lockfile, eslint, tsc, vitest, next build), `compose-smoke` (full `docker compose up --build` from `.env.example`, curl `/readyz` on api and `/healthz` through the web proxy). CI badge in README.
**Fix** — first CI run: frontend \`tsc\` failed on \`LayoutProps\` (a global type Next generates into \`.next/types\`; existed locally from earlier builds, absent on a clean runner). \`typecheck\` now runs \`next typegen && tsc --noEmit\`; reproduced and verified from a clean tree.
**Next** — Phase 0 close-out docs + whole-phase review (Task 8).

## 2026-10-15 — Phase 0, Task 6: frontend skeleton

**Done** — `frontend/` via create-next-app (Next.js 16.4, React 19.3, Tailwind v4, React Compiler, cacheComponents), shadcn/ui initialised (base-nova, neutral), Vitest 5 + Testing Library + jsdom with a homepage test, `typecheck`/`test` scripts, `output: "standalone"`, `/api/:path*` rewrite to `API_INTERNAL_URL` (build arg; rewrites compile at build time). Multi-stage Dockerfile, non-root `veyl` user. Compose `web` service. Verified: `<title>Veyl</title>`, `/api/v1/healthz` and `/readyz` through the proxy.
**Notes** — bumped `@types/node` to ^24 (vitest 5 peer + we run Node 24). Next 16 ships an `AGENTS.md` warning that APIs changed; checked its bundled docs for `rewrites`/`standalone` before using them.
**Next** — GitHub Actions CI (Task 7).

## 2026-10-15 — Phase 0, Task 5: Docker Compose (db, migrate, api)

**Done** — `backend/Dockerfile` (python:3.11-slim, uv-synced venv, non-root uid 10001), `docker-compose.yml`: `db` (pgvector:pg16, init script mounted, TCP healthcheck), one-shot `migrate` (only service with owner creds), `api` (app role, healthcheck on `/readyz`). Verified manually: db healthy → migrate exit 0 → api healthy; `/readyz` 200; api runs as 10001; api env has no owner creds; stopping db → `/readyz` 503.
**Next** — frontend skeleton (Task 6).

## 2026-10-15 — Phase 0, Task 4: fail-closed readiness

**Done** — `GET /api/v1/readyz` runs `SELECT 1` as `veyl_app`; any SQLAlchemy error → 503 `{"status":"unavailable"}` (3s connect timeout keeps it fast). Tests cover reachable (200) and unreachable (503, <10s). 13 tests green.
**Next** — backend Dockerfile + Compose stack (Task 5).

## 2026-10-15 — Phase 0, Task 3: least-privilege Postgres + test DB

**Done** — `docker/postgres/init/01-roles.sh` creates `veyl_owner` (owns `public`, migrations) and `veyl_app` (NOSUPERUSER NOBYPASSRLS NOINHERIT, USAGE only), installs `vector` + `citext`, locks down PUBLIC. Alembic wired to the owner URL (`MIGRATIONS_DATABASE_URL`), no revisions yet. Session-scoped testcontainers fixture mounts the same init script and waits by logging in as `veyl_app` over TCP. Tests: app role has no elevated privileges / can't CREATE TABLE, owner can't bypass RLS, extensions installed, init scripts are LF. 11 tests green.
**Note** — testcontainers 4.15 moved Postgres to `testcontainers.community.postgres`.
**Next** — fail-closed `/readyz` (Task 4).

## 2026-10-15 — Phase 0, Task 2: backend skeleton

**Done** — uv project (`backend/`), `Settings` via pydantic-settings (env-only; fails without `DATABASE_URL`, rejects unknown `ENVIRONMENT`), `create_app()` factory with lazy SQLAlchemy engine, `/api/v1/healthz`, OpenAPI at `/api/v1/docs`. ruff + mypy strict clean; 5 unit tests.
**Note** — swapped dev dep `httpx` → `httpx2`: Starlette's TestClient now warns on httpx.
**Next** — Postgres bootstrap + testcontainers fixture (Task 3; needs Docker Desktop running).

## 2026-10-15 — Phase 0, Task 1: repo hygiene

**Done** — `.gitattributes` forcing LF (repo lives on Windows with `core.autocrlf=true`; CRLF would break the Postgres init script), `.gitignore` (incl. `.env` and the `.superpowers/` plan-execution scratch dir), `.editorconfig`, `.env.example` with dev placeholders only.
**Next** — backend skeleton (Task 2).

## 2026-10-15 — Phase 0 plan written

**Done**
- Spec approved by user.
- Wrote [Phase 0 repo-setup plan](superpowers/plans/2026-10-15-phase-0-repo-setup.md): 8 tasks — hygiene, backend skeleton, least-privilege Postgres bootstrap + testcontainers, fail-closed readiness, Compose, frontend skeleton, CI, close-out.
- New working rule: every commit also updates the root README.

**Blocked**
- Docker Desktop not running locally — needed for integration tests and Compose.

**Next**
- User reviews plan and picks execution method; then execute Task 1.

## 2026-10-15 — Phase 1: requirements & design

**Done**
- Inspected the repo (only a stub README + initial commit).
- Settled key decisions with the user (see [decisions.md](decisions.md) D-001 … D-010).
- Wrote the design spec: [superpowers/specs/2026-10-15-veyl-design.md](superpowers/specs/2026-10-15-veyl-design.md) — architecture, authorization model, schema + ER diagram, pipelines, API contract, threat model mapped to tests, limitations, phases, demo scenario.
- Set up this documentation folder (index, dev log, decision log).

**Next**
- User reviews the spec.
- Write the Phase 0 implementation plan (repo scaffold, tooling, Docker Compose, CI) and execute it.
