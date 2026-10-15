# Phase 0 — Repository Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A clean monorepo where `docker compose up` starts Postgres+pgvector (with least-privilege roles), runs migrations, serves a FastAPI app with health/readiness endpoints, and serves a Next.js app that proxies `/api/*` to it — with tests and GitHub Actions CI all green.

**Architecture:** `backend/` is a uv-managed FastAPI project (app factory, pydantic-settings config, SQLAlchemy engine, Alembic). `frontend/` is a pnpm Next.js App Router project with Tailwind + shadcn/ui and Vitest. `docker/postgres/init/` holds the single DB bootstrap script used by both Docker Compose and the pytest testcontainers fixture, so tests run against exactly the production role setup.

**Tech Stack:** Python 3.11, uv, FastAPI, pydantic-settings, SQLAlchemy 2.0 + psycopg 3, Alembic, pytest, testcontainers, ruff, mypy · Node 24, pnpm 11, Next.js, TypeScript, Tailwind, shadcn/ui, Vitest · Postgres 16 + pgvector · Docker Compose · GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md` (§2, §3.1, §5.1, §12, §13)

## Global Constraints

- **$0, no credit/debit card** for any service (spec §1.3).
- Python **3.11**; Postgres image **`pgvector/pgvector:pg16`**; Node **24**; pnpm **11**.
- API routes are prefixed **`/api/v1`**.
- `api` (and later `worker`) connect as **`veyl_app`** — `NOSUPERUSER NOBYPASSRLS`, not a table owner. Only the one-shot **`migrate`** service gets **`veyl_owner`** credentials (spec §5.1, D-011).
- No secrets in git: only `.env.example` with obvious dev placeholders; `.env` is git-ignored.
- Published container ports bind to **`127.0.0.1`** only.
- Containers run as a **non-root** user.
- **Every commit updates the root `README.md`** (status/progress) and **`docs/devlog.md`**; every commit is pushed to `origin main`.
- Readiness fails closed: if the DB is unreachable, `/api/v1/readyz` returns **503**, never 200.

## Review Focus

1. **Windows CRLF line endings in the Postgres init script** (repo is on Windows with `core.autocrlf=true`) — a CRLF `.sh` silently breaks DB bootstrap. Expect: `.gitattributes` forces LF; a unit test fails if any init script contains `\r` (Task 3).
2. **Missing required config** (e.g. no `DATABASE_URL`) — expect the app to refuse to start with a validation error, not run with a default (Task 2).
3. **DB unreachable at request time** — expect `/readyz` → 503 `{"status":"unavailable"}` quickly (connect timeout), not a hang or 500 trace (Task 4).
4. **App DB role over-privileged** — expect `veyl_app` to be non-superuser, non-BYPASSRLS, unable to create objects in `public` (Task 3).
5. **Postgres "ready" during its init phase** — the image runs init scripts on a socket-only temp server; expect tests and compose to wait for the real TCP server with roles created (Task 3 fixture waits by logging in as `veyl_app`; Task 5 healthcheck uses `-h 127.0.0.1`).

## Deviations from spec (record in `docs/decisions.md` as D-012)

- The `worker` service is **not** added in Phase 0; it arrives in Phase 3 with real job handling (avoids a do-nothing placeholder container).
- `TEST_DATABASE_URL` override is deferred; tests always use testcontainers (CI runners have Docker).

## File map

| Path | Responsibility |
|---|---|
| `.gitattributes`, `.gitignore`, `.editorconfig` | Line endings (LF), ignores, editor defaults |
| `.env.example` | All env vars with dev placeholders |
| `docker-compose.yml` | db, migrate, api, web |
| `docker/postgres/init/01-roles.sh` | Create `veyl_owner`, `veyl_app`, extensions, schema privileges |
| `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version` | Backend project + tool config |
| `backend/app/config.py` | `Settings` (pydantic-settings) |
| `backend/app/db/engine.py` | `create_db_engine()` |
| `backend/app/health.py` | `/healthz`, `/readyz` router |
| `backend/app/main.py` | `create_app()` factory |
| `backend/alembic.ini`, `backend/migrations/{env.py,script.py.mako,versions/}` | Migrations (owner role) |
| `backend/tests/conftest.py` | Session-scoped testcontainers Postgres + migrations |
| `backend/tests/unit/test_config.py`, `test_repo_hygiene.py`, `test_health_liveness.py` | Unit tests |
| `backend/tests/integration/test_db_roles.py`, `test_readiness.py` | DB-backed tests |
| `backend/Dockerfile`, `backend/.dockerignore` | API/migrate image |
| `frontend/**` | Next.js app, Vitest, Dockerfile |
| `.github/workflows/ci.yml` | backend, frontend, compose-smoke jobs |
| `README.md`, `docs/devlog.md`, `docs/decisions.md` | Living docs |

---

### Task 1: Repository hygiene

**Files:**
- Create: `.gitattributes`, `.gitignore`, `.editorconfig`, `.env.example`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces: env var names used by later tasks — `POSTGRES_PASSWORD`, `VEYL_OWNER_PASSWORD`, `VEYL_APP_PASSWORD`, `DB_PORT`, `ENVIRONMENT`, `LOG_LEVEL`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.

- [ ] **Step 1: Write `.gitattributes`**

```gitattributes
* text=auto eol=lf
*.sh text eol=lf
*.sql text eol=lf
*.png binary
*.jpg binary
*.pdf binary
*.ico binary
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
# env
.env
.env.*
!.env.example

# python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# node
node_modules/
.next/
out/
coverage/
*.tsbuildinfo
next-env.d.ts

# app data
backend/uploads/

# os/editor
.DS_Store
Thumbs.db
.idea/
.vscode/
```

- [ ] **Step 3: Write `.editorconfig`**

```ini
root = true

[*]
end_of_line = lf
insert_final_newline = true
charset = utf-8
indent_style = space
indent_size = 2
trim_trailing_whitespace = true

[*.py]
indent_size = 4

[*.md]
trim_trailing_whitespace = false
```

- [ ] **Step 4: Write `.env.example`**

```dotenv
# Copy to .env and change the passwords. Use letters/digits/hyphens only
# (they are embedded in database URLs).

# --- Database (Postgres superuser is used ONLY inside the db container for bootstrap)
POSTGRES_PASSWORD=change-me-postgres
# Owner role: migrations only (the one-shot `migrate` service)
VEYL_OWNER_PASSWORD=change-me-owner
# App role: api + worker. No superuser, no BYPASSRLS.
VEYL_APP_PASSWORD=change-me-app
# Host port for Postgres (bound to 127.0.0.1)
DB_PORT=5432

# --- API
ENVIRONMENT=development
LOG_LEVEL=info

# --- LLM (used from Phase 4). Any OpenAI-compatible endpoint; all free, no card.
# Groq (default):  https://api.groq.com/openai/v1      model e.g. llama-3.3-70b-versatile
# Gemini:          https://generativelanguage.googleapis.com/v1beta/openai/
# Ollama (local):  http://host.docker.internal:11434/v1  (LLM_API_KEY=ollama)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=
```

- [ ] **Step 5: Renormalise line endings and verify `.env` is ignored**

Run:
```bash
git add --renormalize .
git check-ignore -v .env   # expect: .gitignore:2:.env  .env
git ls-files --eol docs/ | head   # expect i/lf w/lf
```

- [ ] **Step 6: Update `README.md` with a project overview + status table**

```markdown
# Veyl

**Permission-aware, multi-tenant RAG.** Veyl answers questions over internal company documents while enforcing tenant isolation and document-level permissions *inside retrieval* — unauthorized chunks never reach the LLM — and audits every retrieval.

Runs entirely on free tiers: local embeddings (bge-small via fastembed), any OpenAI-compatible free LLM (Groq by default; Gemini or local Ollama also work).

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo setup: tooling, Docker Compose, CI | 🚧 in progress |
| 1 | Architecture & threat model ([spec](docs/superpowers/specs/2026-10-15-veyl-design.md)) | ✅ done |
| 2 | Schema, RLS, auth, RBAC | ⏳ |
| 3 | Ingestion, embeddings, filtered retrieval | ⏳ |
| 4 | LLM, citations, conversations, audit | ⏳ |
| 5 | Dashboard | ⏳ |
| 6 | Security test suite, demo, docs | ⏳ |

## Documentation

See [docs/](docs/README.md) — design spec, decision log, dev log, plans.
```

Append to the top of `docs/devlog.md` (below the `# Dev log` intro):

```markdown
## 2026-10-15 — Phase 0, Task 1: repo hygiene

**Done** — `.gitattributes` (force LF; repo lives on Windows with `core.autocrlf=true`), `.gitignore`, `.editorconfig`, `.env.example`; README status table.
**Next** — backend skeleton.
```

- [ ] **Step 7: Commit and push**

```bash
git add -A
git commit -m "chore: add repo hygiene files and env example"
git push origin main
```

---

### Task 2: Backend skeleton — config, app factory, liveness

**Files:**
- Create: `backend/pyproject.toml`, `backend/.python-version`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/db/__init__.py`, `backend/app/db/engine.py`, `backend/app/health.py`, `backend/app/main.py`, `backend/tests/unit/test_config.py`, `backend/tests/unit/test_health_liveness.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - `app.config.Settings(database_url: str, environment: Literal["development","test","production"] = "development", log_level: str = "info")` — reads env vars `DATABASE_URL`, `ENVIRONMENT`, `LOG_LEVEL`; no `.env` file loading.
  - `app.db.engine.create_db_engine(database_url: str) -> sqlalchemy.Engine` (lazy; 3s connect timeout).
  - `app.main.create_app(settings: Settings | None = None) -> FastAPI` — stores engine at `app.state.engine`; uvicorn target `app.main:create_app --factory`.
  - `app.health.router` with `GET /healthz` → `{"status": "ok"}` (readiness added in Task 4).

- [ ] **Step 1: Initialise the uv project**

```bash
cd backend
uv init --bare --python 3.11 --name veyl-backend
uv add fastapi "uvicorn[standard]" pydantic-settings "sqlalchemy>=2.0" "psycopg[binary]" alembic
uv add --dev pytest httpx ruff mypy "testcontainers[postgres]"
echo 3.11 > .python-version
```

Then append tool config to `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
markers = ["integration: needs Docker (testcontainers Postgres)"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "SIM", "RUF"]
ignore = ["S101"]  # assert is fine in tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S105", "S106"]  # hardcoded test passwords

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
packages = ["app"]
```

- [ ] **Step 2: Write failing tests**

`backend/tests/unit/test_config.py`:
```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("ENVIRONMENT", "test")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert s.environment == "test"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()
```

`backend/tests/unit/test_health_liveness.py`:
```python
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthz_is_ok_without_database() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    with TestClient(app) as client:
        response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_served_under_api_prefix() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    with TestClient(app) as client:
        assert client.get("/api/v1/openapi.json").status_code == 200
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 4: Implement**

`backend/app/__init__.py`: empty file. `backend/app/db/__init__.py`: empty file.

`backend/app/config.py`:
```python
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Read from environment variables only (no .env loading):
    Docker Compose injects them, so a missing value fails loudly instead of defaulting."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "info"
```

`backend/app/db/engine.py`:
```python
from sqlalchemy import Engine, create_engine


def create_db_engine(database_url: str) -> Engine:
    """Create a lazily-connecting engine. The short connect timeout keeps readiness
    checks fast when the database is down."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
```

`backend/app/health.py`:
```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}
```

`backend/app/main.py`:
```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import health
from app.config import Settings
from app.db.engine import create_db_engine

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url)
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Veyl API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.include_router(health.router, prefix=API_PREFIX)
    return app
```

- [ ] **Step 5: Run tests, lint, types**

Run: `uv run pytest tests/unit -v && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: 5 passed; ruff and mypy clean (run `uv run ruff format .` first if format check complains).

- [ ] **Step 6: Update README + devlog, commit, push**

README: add a **Development → Backend** section:
```markdown
## Development

### Backend (Python 3.11, uv)
```bash
cd backend
uv sync
uv run pytest            # unit + integration (integration needs Docker running)
uv run ruff check . && uv run mypy
```
```
Devlog entry: "Phase 0, Task 2: FastAPI app factory, Settings (fails without DATABASE_URL), /api/v1/healthz; ruff + mypy strict."

```bash
git add -A
git commit -m "feat(backend): FastAPI skeleton with settings and liveness endpoint"
git push origin main
```

---

### Task 3: Postgres bootstrap (least-privilege roles) + test database fixture

**Files:**
- Create: `docker/postgres/init/01-roles.sh`, `backend/tests/__init__.py` (empty — makes `tests.conftest` importable), `backend/tests/conftest.py`, `backend/tests/unit/test_repo_hygiene.py`, `backend/tests/integration/test_db_roles.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/.gitkeep`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: env names from Task 1.
- Produces:
  - DB roles `veyl_owner` (owns `public` schema) and `veyl_app`; extensions `vector`, `citext`.
  - pytest fixture `database_urls` (session scope) → `DatabaseUrls(owner: str, app: str)`, both `postgresql+psycopg://…` URLs, migrations already at `head`.
  - Alembic: `env.py` takes URL from `config.get_main_option("sqlalchemy.url")` else env `MIGRATIONS_DATABASE_URL`.

- [ ] **Step 1: Write failing tests**

`backend/tests/unit/test_repo_hygiene.py`:
```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_DIR = REPO_ROOT / "docker" / "postgres" / "init"


def test_postgres_init_dir_has_scripts() -> None:
    assert any(INIT_DIR.glob("*.sh"))


def test_postgres_init_scripts_use_lf_line_endings() -> None:
    # CRLF in a shell script breaks Postgres bootstrap silently on Windows checkouts.
    for script in INIT_DIR.iterdir():
        assert b"\r" not in script.read_bytes(), f"{script.name} has CRLF line endings"
```

`backend/tests/integration/test_db_roles.py`:
```python
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import DatabaseUrls

pytestmark = pytest.mark.integration


def test_app_role_has_no_elevated_privileges(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    engine.dispose()
    assert tuple(row) == (False, False, False, False)


def test_app_role_cannot_create_tables(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with pytest.raises(ProgrammingError), engine.begin() as conn:
        conn.execute(text("CREATE TABLE should_fail (id int)"))
    engine.dispose()


def test_owner_role_cannot_bypass_rls(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.owner)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    engine.dispose()
    assert tuple(row) == (False, False)


def test_required_extensions_installed(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with engine.connect() as conn:
        names = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    engine.dispose()
    assert {"vector", "citext"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_repo_hygiene.py tests/integration/test_db_roles.py -v`
Expected: FAIL — init dir missing; `fixture 'database_urls' not found`.

- [ ] **Step 3: Write the bootstrap script**

`docker/postgres/init/01-roles.sh` (mark executable: `git update-index --chmod=+x docker/postgres/init/01-roles.sh` after `git add`):
```sh
#!/bin/sh
# Runs once, on first database initialisation, as the Postgres superuser.
# Creates least-privilege roles: veyl_owner (migrations) and veyl_app (api/worker).
set -e

if [ -z "$VEYL_OWNER_PASSWORD" ] || [ -z "$VEYL_APP_PASSWORD" ]; then
  echo "VEYL_OWNER_PASSWORD and VEYL_APP_PASSWORD must be set" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$VEYL_OWNER_PASSWORD" \
  -v app_pw="$VEYL_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
CREATE ROLE veyl_owner LOGIN PASSWORD :'owner_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE veyl_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;

-- Extensions need superuser; create them here so migrations don't.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"dbname" TO veyl_owner, veyl_app;

ALTER SCHEMA public OWNER TO veyl_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO veyl_app;

-- Functions created by migrations are not executable by default; grant explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE veyl_owner IN SCHEMA public
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
SQL
```

- [ ] **Step 4: Set up Alembic**

Run: `uv run alembic init migrations` then replace generated files.

`backend/alembic.ini` (keep the generated logging sections; set):
```ini
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
# URL comes from MIGRATIONS_DATABASE_URL (owner role). Never the app role.
sqlalchemy.url =
```

`backend/migrations/env.py`:
```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # set to models' MetaData once models exist (Phase 2)


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("MIGRATIONS_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATIONS_DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Keep the generated `script.py.mako`; add `migrations/versions/.gitkeep`. Exclude migrations from mypy strictness by leaving `packages = ["app"]` (already scoped).

- [ ] **Step 5: Write the fixture**

`backend/tests/conftest.py`:
```python
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[1]
INIT_DIR = BACKEND_DIR.parent / "docker" / "postgres" / "init"

OWNER_PASSWORD = "owner-test-pw"
APP_PASSWORD = "app-test-pw"


@dataclass(frozen=True)
class DatabaseUrls:
    owner: str
    app: str


def _wait_for_login(url: str, timeout_s: float = 60.0) -> None:
    """The image runs init scripts on a socket-only temporary server, then restarts.
    Logging in over TCP as veyl_app proves the real server is up AND roles exist."""
    deadline = time.monotonic() + timeout_s
    engine = create_engine(url)
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
    finally:
        engine.dispose()


def _migrate(owner_url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", owner_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database_urls() -> Iterator[DatabaseUrls]:
    container = (
        PostgresContainer(
            "pgvector/pgvector:pg16",
            username="postgres",
            password="postgres-test-pw",
            dbname="veyl",
        )
        .with_env("VEYL_OWNER_PASSWORD", OWNER_PASSWORD)
        .with_env("VEYL_APP_PASSWORD", APP_PASSWORD)
        .with_volume_mapping(str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        base = f"{host}:{port}/veyl"
        urls = DatabaseUrls(
            owner=f"postgresql+psycopg://veyl_owner:{OWNER_PASSWORD}@{base}",
            app=f"postgresql+psycopg://veyl_app:{APP_PASSWORD}@{base}",
        )
        _wait_for_login(urls.app)
        _migrate(urls.owner)
        yield urls
```

- [ ] **Step 6: Run tests (Docker must be running)**

Run: `uv run pytest -v`
Expected: all unit tests + 4 `test_db_roles` tests PASS. If the container's wait fails, inspect with `docker logs` on the testcontainers container before changing code.

- [ ] **Step 7: Lint, README, devlog, commit, push**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`

README → Status: note "least-privilege DB roles verified by tests". Devlog: "Phase 0, Task 3: Postgres bootstrap script (veyl_owner/veyl_app, vector+citext), Alembic wired to owner role, testcontainers fixture reusing the same init script; role-privilege tests."

```bash
git add -A
git update-index --chmod=+x docker/postgres/init/01-roles.sh
git commit -m "feat(db): least-privilege Postgres roles, Alembic, testcontainers fixture"
git push origin main
```

---

### Task 4: Readiness endpoint (fails closed)

**Files:**
- Modify: `backend/app/health.py`, `README.md`, `docs/devlog.md`
- Create: `backend/tests/integration/test_readiness.py`

**Interfaces:**
- Consumes: `create_app`, `Settings`, `database_urls` fixture.
- Produces: `GET /api/v1/readyz` → 200 `{"status":"ready"}` if `SELECT 1` succeeds as the app role, else 503 `{"status":"unavailable"}`.

- [ ] **Step 1: Write failing tests**

`backend/tests/integration/test_readiness.py`:
```python
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import DatabaseUrls


@pytest.mark.integration
def test_readyz_ready_when_database_reachable(database_urls: DatabaseUrls) -> None:
    app = create_app(Settings(database_url=database_urls.app))
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_fails_closed_when_database_unreachable() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://veyl_app:x@127.0.0.1:1/veyl"))
    started = time.monotonic()
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert time.monotonic() - started < 10
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_readiness.py -v`
Expected: FAIL — 404 for `/api/v1/readyz`.

- [ ] **Step 3: Implement** — replace `backend/app/health.py`:

```python
import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: dependencies are reachable. Fails closed with 503."""
    engine: Engine = request.app.state.engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("readiness check failed: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
```

- [ ] **Step 4: Run full suite + lint**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS, clean.

- [ ] **Step 5: README (document `/api/v1/healthz` and `/readyz`), devlog, commit, push**

```bash
git add -A
git commit -m "feat(backend): readiness endpoint that fails closed when DB is unreachable"
git push origin main
```

---

### Task 5: Backend container + Docker Compose (db, migrate, api)

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `docker-compose.yml`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: `.env` vars (Task 1), init script (Task 3), `create_app` factory.
- Produces: services `db`, `migrate`, `api` (127.0.0.1:8000). `web` added in Task 6.

- [ ] **Step 1: `backend/.dockerignore`**

```
.venv
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
tests
uploads
```

- [ ] **Step 2: `backend/Dockerfile`**

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN useradd --system --uid 10001 --no-create-home veyl
USER veyl

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: `docker-compose.yml`**

```yaml
name: veyl

services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: veyl
      VEYL_OWNER_PASSWORD: ${VEYL_OWNER_PASSWORD:?set VEYL_OWNER_PASSWORD in .env}
      VEYL_APP_PASSWORD: ${VEYL_APP_PASSWORD:?set VEYL_APP_PASSWORD in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/postgres/init:/docker-entrypoint-initdb.d:ro
    ports:
      - "127.0.0.1:${DB_PORT:-5432}:5432"
    healthcheck:
      # -h forces TCP: the init-phase temp server only listens on a socket.
      test: ["CMD-SHELL", "pg_isready -U postgres -d veyl -h 127.0.0.1"]
      interval: 3s
      timeout: 3s
      retries: 30

  migrate:
    build: ./backend
    command: ["alembic", "upgrade", "head"]
    environment:
      MIGRATIONS_DATABASE_URL: postgresql+psycopg://veyl_owner:${VEYL_OWNER_PASSWORD}@db:5432/veyl
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

  api:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+psycopg://veyl_app:${VEYL_APP_PASSWORD}@db:5432/veyl
      ENVIRONMENT: ${ENVIRONMENT:-development}
      LOG_LEVEL: ${LOG_LEVEL:-info}
    depends_on:
      migrate:
        condition: service_completed_successfully
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readyz', timeout=3)"]
      interval: 5s
      timeout: 5s
      retries: 20

volumes:
  pgdata:
```

- [ ] **Step 4: Verify end-to-end**

Run (repo root):
```bash
cp .env.example .env
docker compose up -d --build db migrate api
docker compose ps                     # db healthy, migrate exited (0), api healthy
curl -fsS http://127.0.0.1:8000/api/v1/readyz    # {"status":"ready"}
docker compose exec api id -u          # 10001 (non-root)
docker compose logs migrate | tail -5  # alembic ran without error
```
Expected: as commented. Then `docker compose down`.

- [ ] **Step 5: README Quickstart, devlog, commit, push**

README → add **Quickstart** (`cp .env.example .env`, `docker compose up --build`, URLs). Devlog entry.

```bash
git add -A
git commit -m "feat: backend Dockerfile and Compose stack (db, migrate, api)"
git push origin main
```

---

### Task 6: Frontend skeleton — Next.js, Tailwind, shadcn/ui, Vitest, API proxy, container

**Files:**
- Create: `frontend/**` (via create-next-app), `frontend/vitest.config.mts`, `frontend/src/test/setup.ts`, `frontend/src/app/page.test.tsx`, `frontend/Dockerfile`, `frontend/.dockerignore`
- Modify: `frontend/next.config.ts`, `frontend/package.json` (scripts), `frontend/src/app/page.tsx`, `frontend/src/app/layout.tsx` (title), `docker-compose.yml`, `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: API at `API_INTERNAL_URL` (default `http://localhost:8000`; `http://api:8000` in Compose, baked at build time because Next rewrites are compiled).
- Produces: `/api/:path*` on the web origin proxied to the API; scripts `lint`, `typecheck`, `test`, `build`.

- [ ] **Step 1: Scaffold**

```bash
pnpm create next-app@latest frontend --ts --tailwind --eslint --app --src-dir --import-alias "@/*" --use-pnpm --yes
cd frontend
pnpm dlx shadcn@latest init -d
pnpm add -D vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @testing-library/dom vite-tsconfig-paths
```
If pnpm asks to approve build scripts (e.g. `sharp`, `unrs-resolver`), approve them with `pnpm approve-builds` so CI installs identically.

- [ ] **Step 2: Scripts in `frontend/package.json`**

```json
"scripts": {
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "eslint .",
  "typecheck": "tsc --noEmit",
  "test": "vitest run"
}
```

- [ ] **Step 3: Vitest config**

`frontend/vitest.config.mts`:
```ts
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

`frontend/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Failing test**

`frontend/src/app/page.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";

describe("Home", () => {
  it("shows the product name and what it does", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1, name: "Veyl" })).toBeInTheDocument();
    expect(screen.getByText(/permission-aware/i)).toBeInTheDocument();
  });
});
```

Run: `pnpm test` → Expected: FAIL (default create-next-app page has no "Veyl" heading).

- [ ] **Step 5: Implement page, title, proxy, standalone output**

`frontend/src/app/page.tsx`:
```tsx
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 px-6">
      <h1 className="text-4xl font-semibold tracking-tight">Veyl</h1>
      <p className="text-muted-foreground text-lg">
        Permission-aware, multi-tenant retrieval-augmented generation. Answers come only from
        documents you are allowed to see.
      </p>
    </main>
  );
}
```

In `frontend/src/app/layout.tsx` set:
```ts
export const metadata: Metadata = {
  title: "Veyl",
  description: "Permission-aware multi-tenant RAG",
};
```

`frontend/next.config.ts`:
```ts
import type { NextConfig } from "next";

const apiUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
```

- [ ] **Step 6: Verify**

Run: `pnpm test && pnpm lint && pnpm typecheck && pnpm build`
Expected: 1 test passed; lint/typecheck clean; build succeeds with `.next/standalone`.

- [ ] **Step 7: Container**

`frontend/.dockerignore`:
```
node_modules
.next
coverage
```

`frontend/Dockerfile`:
```dockerfile
FROM node:24-alpine AS base
RUN npm install -g pnpm@11
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S veyl && adduser -S veyl -G veyl
COPY --from=build --chown=veyl:veyl /app/.next/standalone ./
COPY --from=build --chown=veyl:veyl /app/.next/static ./.next/static
COPY --from=build --chown=veyl:veyl /app/public ./public
USER veyl
EXPOSE 3000
CMD ["node", "server.js"]
```

Add to `docker-compose.yml` under `services:`:
```yaml
  web:
    build:
      context: ./frontend
      args:
        API_INTERNAL_URL: http://api:8000
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "127.0.0.1:3000:3000"
```

- [ ] **Step 8: Verify full stack**

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:3000/ | grep -o "<title>Veyl</title>"
curl -fsS http://127.0.0.1:3000/api/v1/healthz     # {"status":"ok"} via the proxy
docker compose down
```

- [ ] **Step 9: README (frontend dev commands, URLs), devlog, commit, push**

```bash
git add -A
git commit -m "feat(frontend): Next.js + Tailwind + shadcn skeleton with Vitest and API proxy"
git push origin main
```

---

### Task 7: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md` (CI badge), `docs/devlog.md`

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest -v

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 11
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint
      - run: pnpm typecheck
      - run: pnpm test
      - run: pnpm build

  compose-smoke:
    needs: [backend, frontend]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cp .env.example .env
      - run: docker compose up -d --build
      - name: API ready
        run: curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:8000/api/v1/readyz
      - name: Web proxies API
        run: curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:3000/api/v1/healthz
      - name: Logs on failure
        if: failure()
        run: docker compose logs
      - run: docker compose down -v
        if: always()
```

- [ ] **Step 2: README badge + devlog, commit, push, watch**

Add under the README title:
```markdown
[![CI](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml/badge.svg)](https://github.com/yatinannam/Veyl/actions/workflows/ci.yml)
```

```bash
git add -A
git commit -m "ci: backend, frontend, and compose smoke-test workflow"
git push origin main
gh run watch --exit-status
```
Expected: all three jobs green. If red, read the failing step log (`gh run view --log-failed`) and fix before moving on.

---

### Task 8: Close out Phase 0 documentation

**Files:**
- Modify: `README.md`, `docs/devlog.md`, `docs/decisions.md`, `docs/superpowers/specs/2026-10-15-veyl-design.md` (§13 Phase 0 row), `docs/README.md`

- [ ] **Step 1: Record D-012 in `docs/decisions.md`**

```markdown
| D-012 | 2026-10-15 | Phase 0 ships **without the worker service**; it arrives in Phase 3 with real job handling. `TEST_DATABASE_URL` override deferred — tests always use testcontainers. | Idle placeholder worker container | Avoid placeholder services; CI has Docker. |
```

- [ ] **Step 2: Spec §13** — Phase 0 "Done when" becomes: "`docker compose up` starts db, migrate, api, web with health endpoints; CI green (worker added in Phase 3)."

- [ ] **Step 3: README** — Phase 0 row ✅; repository layout section; links to plan. **docs/README.md** — add the plan link. **Devlog** — Phase 0 summary + "Next: Phase 2 plan (schema, RLS, auth, RBAC)".

- [ ] **Step 4: Commit, push, confirm CI**

```bash
git add -A
git commit -m "docs: close out Phase 0"
git push origin main
gh run watch --exit-status
```
