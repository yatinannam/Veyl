# Hardening: open review items

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the minor findings deferred by the six phase reviews, or record why one stays open.

**Architecture:** No new components. Each fix lands where the defect is, with a test that fails first. Items are grouped into six batches by area so each commit is reviewable on its own.

**Tech Stack:** as the project (FastAPI, SQLAlchemy Core, Postgres, Next.js, Docker Compose, GitHub Actions).

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md`; findings come from `docs/devlog.md` (review entries) and `docs/threat-model.md` (open review items).

## Global Constraints

- No emojis; $0; every commit updates `README.md` and `docs/devlog.md` and is pushed; containers stopped when work ends.
- Every behaviour change gets a test that fails before the fix. Test-only items (weak tests) are proven by a temporary mutation that the strengthened test catches.
- Nothing weakens an existing guarantee; the full backend and frontend suites stay green.

## Triage

Review items by phase (P0 = Phase 0 review, item number in the dev log). "Done" items were fixed in a later phase and are listed so the record is complete.

| Item | Finding | Verdict |
|---|---|---|
| P0-1 | `/readyz` has no statement timeout | Fix (Batch E) |
| P0-2 | `LOG_LEVEL` not applied to the API | Fix (Batch E) |
| P0-3 | `database_url` not a secret type | Done (`SecretStr`) |
| P0-4 | `.env*` missing from `.dockerignore` files | Fix (Batch E) |
| P0-5 | Compose hardening: drop capabilities, no-new-privileges, read-only root | Fix (Batch E) |
| P0-6 | PUBLIC may connect to the `postgres` maintenance database | Fix (Batch E) |
| P0-7 | CI actions not pinned by SHA; no job timeouts | Fix (Batch E) |
| P0-8 | "API has no owner credentials" not checked automatically | Fix (Batch E) |
| P0-10 | prettier, pytest-cov from the spec's tooling list | Won't fix: ESLint and the TypeScript checker already gate the frontend; coverage numbers add no guarantee the security tests do not |
| P2-1 | Caller's admin status checked before the membership lock | Fix (Batch B) |
| P2-2 | `FOR UPDATE` conflicts with key-share locks | Done (`FOR NO KEY UPDATE`) |
| P2-3 | Session expiry test tolerates 5 s | Fix (Batch B) |
| P2-4 | No test for a non-ASCII CSRF cookie | Fix (Batch B) |
| P2-5 | NUL bytes in login fields cause a 500 | Fix (Batch A) |
| P2-6 | Rate-limit check and record are not atomic | Fix (Batch B): count the attempt before verifying, reset on success |
| P2-7 | Login keeps an already-presented session; logout with a dead session leaves cookies | Fix (Batch B) |
| P2-8 | Provisioning reports any integrity error as a duplicate slug; CLI email unvalidated | Fix (Batch B) |
| P3-1, P3-5 | Python re-check rules; cache key shape | Done (Phase 4) |
| P3-2 | NUL bytes or lone surrogates in PDF text retried as transient | Fix (Batch A): clean the extracted text |
| P3-3 | NUL in an upload title causes a 500 | Fix (Batch A) |
| P3-4 | Two ingestion tests would pass without their guards | Fix (Batch D) |
| P3-6 | No lease token on jobs: a reclaimed job's first worker can still write | Fix (Batch D): persist only while holding the claimed attempt |
| P3-7 | Upload `allowed_user_ids` not capped | Fix (Batch D) |
| P3-8 | Embedding model revision not pinned | Won't fix: fastembed is pinned in `uv.lock`, the model is baked into the image at build time, and a real-model test checks it loads and embeds |
| P3-9 | Chunk ACL equality not enforced by a trigger | Won't fix: `acl_version` matching plus the Python re-check already make a mismatched chunk invisible; a trigger would duplicate them on every chunk write |
| P4-1 | Deeply nested provider JSON gives an unaudited 500 | Fix (Batch C) |
| P4-2 | Delimiter neutralisation misses Unicode look-alikes | Fix (Batch C) |
| P4-3 | Old `[S#]` markers in replayed history | Fix (Batch C) |
| P4-4 | Placeholder `AUDIT_HMAC_KEY` accepted in production | Fix (Batch E) |
| P4-5 | LLM timeout is per socket operation | Fix (Batch C): total deadline |
| P4-6 | Conversation deleted mid-request gives a 500 | Fix (Batch C) |
| P4-7 | Test gaps: zero-result audit; cache test passes without a cache | Fix (Batch C) |
| P4-8 | `rag.denied` omits the attempted conversation id | Fix (Batch C) |
| P5-1 to P5-8 | Dashboard items | Done (Phase 6, Task 4) |
| P5-9 | Concurrent permission edits: the later save wins silently | Fix (Batch F): optimistic concurrency on `acl_version` |
| P6-1 | Seed partial failure and timeout messages | Fix (Batch F) |
| P6-2 | Citation dagger reference crosses newlines | Fix (Batch C) |
| P6-3 | Demo admin test can pass vacuously | Fix (Batch F) |

## Batches

### Batch A: NUL bytes and invalid text

- [ ] Tests: login with NUL in `tenant_slug` or `email` returns 422 `validation_error`; upload with NUL in the title returns 422; a PDF whose text contains NUL bytes and a lone surrogate ingests (cleaned text), not retried.
- [ ] Fix: request models and the upload form reject NUL characters; extracted text has NUL removed and lone surrogates replaced before chunking.

### Batch B: authentication and administration

- [ ] Tests: a caller demoted after passing the action check cannot change users (re-checked under the lock); expiry is exactly `SESSION_TTL_MINUTES` by the database clock; a non-ASCII CSRF cookie is rejected cleanly; a concurrent burst of wrong passwords cannot exceed the account limit; signing in while presenting another session revokes it; logout with a dead session clears both cookies (204); provisioning reports a duplicate email distinctly; the CLI rejects a malformed admin email.
- [ ] Fix each in `users/service.py`, `auth/service.py`, `auth/ratelimit.py`, `auth/router.py`, `provisioning.py`, `cli.py`.

### Batch C: answering questions

- [ ] Tests: any unexpected exception from the LLM client becomes `503 llm_unavailable` and is audited; `<source` written with zero-width characters, fullwidth letters, or HTML entities is neutralised; `[S#]` markers are stripped from replayed history; a provider that trickles bytes past the total deadline fails with `llm_unavailable`; deleting the conversation during the model call returns 404 and audits it; `rag.query` with zero results is audited with `result_count=0`; the end-to-end cache test fails when the cache is disabled; `rag.denied` records the conversation id; a dagger reference cannot swallow text across a newline.
- [ ] Fix in `rag/service.py`, `llm/prompt.py`, `llm/client.py`, `llm/citations.py`, `conversations/service.py`.

### Batch D: ingestion

- [ ] Tests: strengthen the deleted-before-processing and ingestion-race tests (proven by mutation); a worker whose job was reclaimed cannot persist; uploads with more than 500 `allowed_user_ids` are rejected.
- [ ] Fix in `ingestion/pipeline.py` and the upload route.

### Batch E: platform

- [ ] `/readyz` sets a statement timeout; `LOG_LEVEL` configures the API's loggers; the placeholder audit key is rejected when `ENVIRONMENT=production` (tests for each).
- [ ] `.env*` in both `.dockerignore` files; Compose services drop all capabilities and set `no-new-privileges` (read-only root filesystem for `api` and `worker` with a tmpfs `/tmp`); the Postgres init script revokes PUBLIC's connect right on the `postgres` database; CI actions pinned by commit SHA with job timeouts; compose-smoke asserts that the `api` and `worker` containers have no owner credentials. Verified by a full local stack run and CI.

### Batch F: dashboard and demo

- [ ] Tests: `PUT /documents/{id}/permissions` with a stale `expected_acl_version` returns 409 `conflict`; the permission editor sends the version it loaded and explains a conflict; a seed failure after tenants exist says the demo is incomplete and how to reset; a wait timeout says the data is complete and still processing; the demo admin test asserts a restricted citation exists.
- [ ] Fix in `documents/`, the frontend permission editor, `demo/seed.py`, `cli.py`, the demo test.

### Close-out

- [ ] Threat model "Open review items" reduced to what remains (the won't-fix rulings with reasons); devlog entry; README status; CI green; containers stopped.
