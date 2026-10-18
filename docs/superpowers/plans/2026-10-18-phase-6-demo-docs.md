# Phase 6 — Demo, security walkthrough, docs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone can clone the repo, follow the README, seed a two-company demo, and watch every security property from the brief hold, in the dashboard and in an automated test; the architecture, threat model, API, and demo are documented.

**Architecture:** A `seed-demo` operator command creates the two tenants with the owner role (the one thing the API deliberately cannot do), then creates users, uploads documents, and sets permissions **through the public HTTP API** as each tenant's admin, using a small stdlib client. The same seed code runs in tests against `TestClient`, so the demo scenario from spec section 14 becomes an end-to-end security test. Docs are distilled from the spec and point at the real test files.

**Tech Stack:** Python 3.11 stdlib (`urllib`, `http.cookiejar`), FastAPI TestClient in tests, Docker Compose profiles, GSAP (`gsap`, `@gsap/react`) for a few motion moments, Markdown docs.

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md` (sections 9, 11, 12, 13, 14)

## Global Constraints

- No emojis anywhere. $0: no paid services; GSAP is free under its standard licence (D-050), Lenis is MIT.
- Every commit updates `README.md` and `docs/devlog.md` and is pushed. Containers are stopped when work ends.
- Demo passwords come from `VEYL_DEMO_PASSWORD` (never an argument), at least 12 characters. Demo content is synthetic.
- The seed uses the owner role only to create tenants; everything else goes through the API as the tenant admin.
- Motion respects `prefers-reduced-motion` (no animation at all when set) and never delays content or input.
- Done when (spec section 13): a fresh clone produces the working demo by following the README.

## Review Focus

1. **Seeding twice** — expect a clear message and exit code 1, no duplicate tenants or half-seeded data.
2. **Seed runs before the worker finishes** — the command waits until every demo document is `ready` (or reports which one failed) so the walkthrough works immediately.
3. **Walkthrough with the real embedding model** — every demo question must clear `RETRIEVAL_MIN_SCORE` (0.5) for the documents it should hit; checked live in Task 7.
4. **Reduced motion** — with `prefers-reduced-motion: reduce`, no GSAP tween runs; content is visible immediately. Test in Task 5.
5. **Fresh clone on another project name** — the README steps alone (no leftover volumes or `.env`) bring up the demo. Checked in Task 7.

---

### Task 1: Demo seed command

**Files:**
- Create: `backend/app/demo/__init__.py`, `backend/app/demo/content.py` (tenants, users, documents, ACLs), `backend/app/demo/documents/*.md` (synthetic content), `backend/app/demo/http.py` (`ApiSession` over a `Transport`; `UrllibTransport` with cookie jar, CSRF header, multipart encoding), `backend/app/demo/seed.py` (`seed_demo`), `backend/tests/integration/test_demo_seed.py`, `backend/tests/unit/test_demo_http.py`
- Modify: `backend/app/cli.py` (`seed-demo --api-url`), `docker-compose.yml` (`seed` service, profile `demo`), `README.md`, `docs/devlog.md`

**Interfaces:**
- `Transport.request(method, path, *, json=None, form=None, files=None, headers=None) -> Response(status, body)`; `UrllibTransport(base_url)`; tests adapt `TestClient`.
- `seed_demo(owner_engine, new_transport: Callable[[], Transport], password: str, *, wait_timeout: float) -> DemoSummary` (tenant slugs, user emails, document titles). Raises `AlreadySeeded` when `acme` or `globex` exists.
- Scenario (spec section 14): Acme (`acme`): alice admin, mark manager, mia member; documents *Employee Handbook* (internal, all roles), *Engineering Roadmap* (confidential, managers), *Q3 Board Financials* (restricted, no roles: admins only), *Mark's Performance Review* (confidential, `allowed_user_ids=[mark]`), *IT Help FAQ* (internal, all roles, contains the injected instruction). Globex (`globex`): gina admin, gary member; *Merger Plan* (restricted).

- [ ] **Step 1: Failing tests** — unit: multipart body round-trips (boundary, filename, content type), CSRF header added only on unsafe methods, non-2xx raises `DemoApiError` with the API error code. Integration: `seed_demo` through `TestClient` creates both tenants, five users with the right roles, six documents with the right classification and ACLs (Mark's review shared only with mark), all `ready` after running the ingestor; a second run raises `AlreadySeeded` and changes nothing.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the modules, the CLI command (password from `VEYL_DEMO_PASSWORD`, exit 1 on `AlreadySeeded` with "Demo already seeded"), and the Compose `seed` service (backend image, owner URL, `--api-url http://api:8000`, depends on a healthy `api`, profile `demo`).
- [ ] **Step 4: Backend checks** (ruff, format, mypy, pytest) PASS.
- [ ] **Step 5: README (demo quickstart), devlog, commit, push.**

### Task 2: The demo scenario as a security test

**Files:** Create `backend/tests/security/test_demo_scenario.py`; Modify `README.md`, `docs/devlog.md`

Seed through `TestClient` with `FakeEmbedder` and `RecordingLLM`, run the ingestor, then walk the scenario through the API, asserting on every recorded prompt:

- [ ] **Step 1: Tests** —
  - `test_member_cannot_reach_board_financials`: Mia asks about Q3 revenue; no text unique to *Q3 Board Financials* appears in any prompt; no citation names it.
  - `test_admin_gets_cited_answer_from_board_financials`: Alice's prompt contains the financials and her citations include *Q3 Board Financials* (classification `restricted`).
  - `test_other_tenant_sees_nothing_from_acme`: Gary's prompts contain no Acme text; his document list has only Globex documents.
  - `test_reclassification_takes_effect_on_next_query_and_revert_removes_it`: Alice sets the roadmap to internal for members; Mia's next prompt contains roadmap text; Alice reverts; Mia's very next prompt does not.
  - `test_injected_instruction_cannot_widen_access`: Mia asks the FAQ question; the FAQ (with its injected instruction) is in her prompt inside a source block, financials text is not.
  - `test_personal_share_is_only_for_mark`: Mark's prompt can contain his review; Mia's cannot; Alice's can (admin).
  - `test_audit_trail_shows_the_walkthrough`: Alice lists audit events and sees Mia's `rag.query` events with result counts and no question text.
- [ ] **Step 2: Run** — these exercise existing behaviour, so they should pass; to prove they can fail, temporarily break `chunk_read_filter`'s clearance condition and the Python re-check together (mutation check) and confirm at least the first test fails; restore.
- [ ] **Step 3: README (security tests section), devlog, commit, push.**

### Task 3: CI demo smoke

**Files:** Create `docker/smoke/demo.sh`; Modify `.github/workflows/ci.yml`, `README.md`, `docs/devlog.md`

- [ ] **Step 1:** `demo.sh BASE_URL` (POSIX sh + curl, like the other smoke scripts): logs in as mia, asks "What was Acme's revenue in Q3?", asserts no citation title "Q3 Board Financials"; logs in as alice, asks the same, asserts a citation to it (the stub LLM cites S1); logs in as gary and asserts the documents list has no Acme titles.
- [ ] **Step 2:** In `compose-smoke`, after the existing query step: `docker compose run --rm -e VEYL_DEMO_PASSWORD=... seed`, then `sh docker/smoke/demo.sh http://127.0.0.1:3000`.
- [ ] **Step 3:** Push; CI green; README, devlog.

### Task 4: Dashboard polish (Phase 5 deferred minors)

**Files:** Modify `frontend/src/lib/api.ts` or `frontend/src/lib/use-api.ts` and `frontend/src/components/session.tsx`, `frontend/src/components/documents/upload-dialog.tsx`, `frontend/src/app/(app)/chat/page.tsx`, `frontend/src/components/chat/answer.tsx`, `frontend/src/components/chat/composer.tsx`, `frontend/src/app/(app)/admin/users/page.tsx`, `frontend/src/lib/audit.ts`, `frontend/src/components/documents/acl-fields.tsx`, `frontend/src/components/documents/permission-editor.tsx`; tests beside each

Each item gets a failing test first:
- [ ] A 401 from any API call after load sends the user to `/login?next=<current path>`.
- [ ] Cancel or close resets the upload form, including the file input.
- [ ] Chat keeps the pending question visible until the new conversation has loaded (no empty-state flash) and shows conversation load errors.
- [ ] Source markers inside bold text become links; numbered lists render as `<ol>`.
- [ ] Enter during IME composition does not submit.
- [ ] Deactivating a person asks for confirmation.
- [ ] Audit date filters use local-day boundaries (local midnight to the end of the local day, converted to UTC).
- [ ] The permission editor lists grants to people missing from the directory as "Former or inactive person" entries that can be removed.
- [ ] Frontend checks; README, devlog, commit, push.

### Task 5: Motion with GSAP

**Files:** Create `frontend/src/lib/motion.ts` (`useReveal`-style helpers on top of `useGSAP` with `gsap.matchMedia`), tests; Modify `frontend/package.json`, `frontend/src/app/login/page.tsx` (levels panel), `frontend/src/components/chat/answer.tsx` (source cards), `frontend/src/components/classification.tsx` (clearance meter fill); `docs/decisions.md` (D-050, D-051)

- [ ] **Step 1: Failing tests** — with `matchMedia("(prefers-reduced-motion: reduce)")` matching, the helper creates no tweens and elements keep their final styles; without it, it animates from a hidden state to visible (assert `gsap.from` called with the expected targets via a spy on the helper's GSAP instance).
- [ ] **Step 2: Implement** — three moments only: the sign-in panel's four levels appear in order once; a new answer's source cards slide in one after another; the clearance meter fills once per session load. Durations under 400 ms, no delay before content is readable, nothing on scroll. Lenis is not used: the app's screens are tables, chat, and forms with their own scroll areas, and smooth-scroll easing would make them feel slower (D-051).
- [ ] **Step 3:** Frontend checks; screenshots of the three moments reviewed; README, devlog, commit, push.

### Task 6: Documentation

**Files:** Create `docs/architecture.md`, `docs/threat-model.md`, `docs/api.md`, `docs/demo.md`; Modify `docs/README.md`, `README.md` (polish), `docs/devlog.md`, spec section 13

- [ ] `architecture.md`: components and trust boundaries (edge, web, api, worker, db roles), request lifecycle, the query pipeline step by step, ingestion, data model summary, where each permission check lives; a Mermaid diagram.
- [ ] `threat-model.md`: assets, actors, trust boundaries; T1 to T16 with mitigation and the **actual** test files and test names that cover each (verified by grep); known limitations (spec section 11); open review items (deferred minors from all phases).
- [ ] `api.md`: every endpoint with method, path, who may call it, request and response examples (curl through the edge with the cookie jar and CSRF header), error envelope and codes.
- [ ] `demo.md`: seeding, the accounts, the walkthrough in the dashboard step by step with what to expect, the same checks with curl, and how to reset (`docker compose down -v`).
- [ ] README: lead with what Veyl is and the guarantee, quickstart with demo in a few commands, screenshots, links into docs; move long sections into the docs.
- [ ] Commit, push.

### Task 7: Fresh clone, live demo, review, close-out

- [ ] Clone the repo into a scratch folder, follow the README exactly with a separate Compose project name, with the user's Groq key and with the stub; confirm the demo walkthrough works; fix anything the README misses. Tear down only that scratch project.
- [ ] Live walkthrough with the real model in the dashboard; check every demo question clears the score threshold; capture demo screenshots for `demo.md` and review them.
- [ ] Final whole-phase review (independent reviewer); fix Critical and Important with failing tests first; minors to the devlog.
- [ ] Spec section 13 Phase 6 done; README Phase 6 done; devlog summary; stop containers; LinkedIn post for the user.
