# Decision log

Lightweight ADRs. Status is `accepted` unless noted. Details live in the [design spec](superpowers/specs/2026-10-15-veyl-design.md).

| ID | Date | Decision | Alternatives considered | Why |
|---|---|---|---|---|
| D-001 | 2026-10-15 | **Everything must be $0 with no credit/debit card.** | Paid APIs (Claude, OpenAI) | Hard user constraint. Drives D-002, D-003, D-009. |
| D-002 | 2026-10-15 | LLM via one **OpenAI-compatible client**; default **Groq free tier**; Gemini AI Studio / Ollama / OpenRouter `:free` as config-only alternatives | Claude API (paid), OpenAI (paid), Ollama-only (heavy on laptop) | Free without card; provider swap is just env vars. |
| D-003 | 2026-10-15 | Embeddings: **`BAAI/bge-small-en-v1.5` (384-dim) via fastembed**, run locally | Voyage/OpenAI embeddings (paid), sentence-transformers + PyTorch (huge image) | Free, offline, small ONNX runtime. |
| D-004 | 2026-10-15 | Auth: **opaque session token in httpOnly cookie, hashed in Postgres**, argon2id passwords, CSRF double-submit | Stateless JWT, external IdP | Instant revocation; role re-read every request; fail-closed friendly. |
| D-005 | 2026-10-15 | Ingestion: **separate worker + Postgres job table** (`FOR UPDATE SKIP LOCKED`) | FastAPI BackgroundTasks, Redis + Celery | Durable async jobs without extra infrastructure. |
| D-006 | 2026-10-15 | Retrieval authz: **ACL denormalised onto chunks, filtered inside the vector query, re-checked in Python**; tenant isolation also enforced by **Postgres RLS** | Join to source-of-truth ACL per query; store-per-tenant | Matches brief's chunk-metadata requirement, fast, defense in depth. |
| D-007 | 2026-10-15 | Stale-permission defense: `acl_version` on documents + chunks, must match at query time; tenant `policy_version` in cache keys | Rely only on transactional rewrite | Stale chunks become invisible even if a rewrite were missed. |
| D-008 | 2026-10-15 | Retrieval cache stores **chunk IDs only**; content re-fetched through the authz filter on hit | Caching answers / chunk text | Cache structurally cannot leak content. |
| D-009 | 2026-10-15 | Hosting: **local Docker Compose only** for now | Free cloud hosts | Worker + embedding model RAM is tight on free hosts; revisit after the app works. |
| D-010 | 2026-10-15 | Backend data access: **SQLAlchemy 2.0 sync + psycopg 3**; tests on real Postgres via **testcontainers** | Async SQLAlchemy/asyncpg; mocked DB | Simpler transaction/RLS handling; RLS needs a real DB to test. |
| D-011 | 2026-10-15 | Only the one-shot `migrate` service gets DB-owner credentials; api/worker use `veyl_app` (no BYPASSRLS) | API runs migrations on boot | Least privilege — a compromised API can't disable RLS. |
| D-012 | 2026-10-15 | Phase 0 ships **without the worker service**; it arrives in Phase 3 with real job handling. `TEST_DATABASE_URL` override deferred — tests always use testcontainers. | Idle placeholder worker container | Avoid placeholder services; CI runners have Docker. |
| D-013 | 2026-10-15 | Frontend on **Next.js 16.4** with create-next-app defaults kept (React Compiler, `cacheComponents`); type-check runs `next typegen` first | Strip framework defaults | Generated, tested defaults; route types are generated, so a clean checkout needs typegen. |
