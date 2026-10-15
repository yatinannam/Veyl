# Dev log

Newest entries first. Each entry: what was done, what's next, anything blocked.

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
