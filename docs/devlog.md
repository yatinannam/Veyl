# Dev log

Newest entries first. Each entry: what was done, what's next, anything blocked.

## 2026-10-20 — Hardening, Batch F and close-out: every review finding resolved

**Batch F** (tests RED to GREEN unless noted)
- *P5-9:* permission changes use optimistic concurrency. Documents expose `acl_version`; `PUT /documents/{id}/permissions` accepts `expected_acl_version` and answers `409 conflict` ("Someone else changed these permissions...") when it is stale. The dashboard's editor sends the version it loaded; on a conflict it shows the message, loads the latest permissions into the form, and checks the next save against them. Clients that send no version keep the old behaviour.
- *P6-1:* a seed that fails says the demo may be incomplete and how to reset (`docker compose down -v`, start, seed again); a wait timeout is reported as success with the documents still processing; every failed document is named, not only the first.
- *P6-3:* the demo admin test now requires a restricted citation to exist (it passed vacuously on an empty list). Passes, so the walkthrough really cites the financials.

**Close-out** — all deferred findings from the six phase reviews are resolved: 12 were already fixed in later phases, 3 stay open by decision (listed with reasons in the threat model), and the rest were fixed across Batches A to F, each with a failing test first (strengthened tests proven by mutation). 353 backend tests, 57 frontend tests.

## 2026-10-20 — Hardening, Batch E: platform

**Done** (tests RED to GREEN for the code changes)
- *P4-4:* with `ENVIRONMENT=production`, the example `AUDIT_HMAC_KEY` from `.env.example` is refused at start-up (development still accepts it).
- *P0-1:* `/readyz` runs under a 2-second statement timeout, so a database that accepts connections but stalls yields `503` instead of hanging the probe.
- *P0-2:* `LOG_LEVEL` now sets the API's `app` logger (it only applied to the worker).
- *P0-6:* the Postgres init script revokes PUBLIC's connect right on the `postgres` and `template1` databases; the app role is refused there (applies to new database volumes).
- *P0-4:* `.env` and `.env.*` are excluded from both Docker build contexts.
- *P0-5:* every Compose service except Postgres drops all capabilities and sets `no-new-privileges` (the edge keeps only `no-new-privileges`: Caddy's binary carries a file capability and would not execute with every capability dropped); `api` and `worker` run with read-only root filesystems and a tmpfs `/tmp`.
- *P0-7:* CI actions are pinned to the commit their current major tag points to, and each job has a timeout.
- *P0-8:* the Compose smoke job checks that neither `api` nor `worker` can see owner credentials.

**Verified** on a fresh scratch stack with the hardened settings: every service healthy, the demo seeded through the read-only worker, the demo permission checks, sign-in pages, readiness, and the forged-IP rate-limit probe all passed; the scratch stack was then removed. GitHub private vulnerability reporting is now enabled (user approved), so `SECURITY.md`'s reporting link works. 350 backend tests.
**Next** — Batch F (dashboard and demo).

## 2026-10-18 — Hardening, Batch D: ingestion

**Done**
- *P3-6 (lease):* a claim's attempt number is now its lease. Before writing anything (marking processing, storing chunks, recording a failure) the worker locks the job row and checks it still holds that attempt; a worker that stalled past the stale timeout and was overtaken by another can no longer overwrite the new worker's results. Test RED to GREEN.
- *P3-7:* uploads may share a document with at most 500 people (the same limit as permission changes); more is `422 upload_rejected`, checked before the file is read. Test RED to GREEN.
- *P3-4 (weak tests):* the permission-change test now requires chunks to exist before checking them, and a new test runs the race for real: a permission change holds the document row mid-transaction while the worker persists; the worker must wait and then copy the new ACL. Removing the `FOR SHARE` lock makes it fail. The deleted-before-processing test now requires the job to finish cleanly on its first attempt; removing the deleted-document guard makes it fail (before, the missing file made ingestion fail anyway, so the test passed without the guard).

345 backend tests.
**Next** — Batch E (platform).

## 2026-10-18 — Hardening, Batch C: answering questions; README additions

**Done** (tests RED to GREEN unless noted)
- *P4-1:* any exception from the model call, not only the client's own `LLMError` (for example `RecursionError` from a pathological provider response), returns `503 llm_unavailable` and is audited with stage `llm`.
- *P4-2:* delimiter neutralisation removes invisible characters (zero-width space, joiners, word joiner, BOM) and applies NFKC before escaping, so a zero-width space inside `<source`, or a fullwidth `<` or fullwidth letters, are neutralised like the real thing.
- *P4-3:* source markers are stripped from replayed history (an old `[S1]` referred to an earlier turn's sources). The history test's expectation changed accordingly.
- *P4-5:* the LLM timeout is now also a total deadline for the response body; a provider trickling bytes is cut off after the timeout instead of holding a worker for minutes.
- *P4-6:* the conversation is re-checked under a row lock when the exchange is stored; if it was deleted while the model was answering, the result is `404` and a `rag.denied` audit record (`conversation_deleted`) instead of a 500.
- *P4-8:* `rag.denied` details include the conversation id that was named.
- *P6-2:* the marker grammar moved to `app/llm/markers.py` (shared by the citation check and the prompt builder); a dagger reference may not cross a line or exceed 40 characters.
- *P4-7:* a zero-result question is asserted to be audited with `result_count=0` (already worked; test was missing), and the end-to-end cache test now also proves a cache entry exists, so it fails with caching off (mutation check).

**README** — project status section removed; contents made collapsible; new "Key design decisions" table (eight decisions with what they replace and why, linked to the decision log), FAQ, and Security section; test counts updated. Added `SECURITY.md` (private reporting, scope, supported versions). 342 backend tests.
**Next** — Batch D (ingestion).

## 2026-10-18 — Hardening, Batch B: authentication and administration

**Done** (tests RED to GREEN unless noted)
- *P2-1:* creating or changing users re-checks, under the membership lock, that the caller is still an active admin; a demotion committed after the request's session check now wins. The mutual-demotion race test now also accepts `forbidden` for the loser (it was demoted first), and still proves one admin remains.
- *P2-6:* each sign-in attempt is counted before the password is verified, atomically with the limit check, and a success gives its slot back. Ten concurrent wrong passwords against a limit of three now verify at most three (previously all ten).
- *P2-7:* signing in revokes the session the browser already presented; logout clears both cookies even when the session is already dead (previously 401 with cookies left behind).
- *P2-8:* provisioning rejects a malformed admin email. (Reporting other integrity errors distinctly is moot: in a brand-new tenant only the slug can collide.)
- *P2-3:* the session-expiry test now asserts exactly the TTL; computing expiry on the app clock makes it fail (mutation check).
- *P2-4:* a non-ASCII CSRF cookie is rejected with 403; this already worked, the test was missing.

331 backend tests.
**Next** — Batch C (answering questions).

## 2026-10-18 — Hardening, Batch A: NUL bytes and invalid text

**Done** — a shared `SafeText` type rejects NUL characters in sign-in fields, upload titles, questions, new users' emails, and workspace names, so they get `422 validation_error` instead of a 500 from inside a database call (P2-5, P3-3). Extracted document text has NUL characters removed and lone surrogates replaced, so such PDFs ingest instead of failing three times as "transient" (P3-2). Tests RED to GREEN.
**Next** — Batch B (authentication and administration).

## 2026-10-18 — Hardening plan: every deferred review item triaged

**Done** — [hardening plan](superpowers/plans/2026-10-18-hardening-open-items.md): all deferred findings from the six phase reviews listed with a verdict. 12 were already fixed in later phases; 3 stay open with reasons (prettier and coverage tooling, pinning the embedding revision beyond `uv.lock` and the baked image, a chunk-ACL trigger that would duplicate `acl_version` and the Python re-check); the rest are fixed in six batches (input hardening, authentication, answering, ingestion, platform, dashboard and demo).
**Next** — Batch A.

## 2026-10-18 — README rewrite and MIT licence

**Done** — README restructured for a technical reader: centred header with stack badges and quick links; the admin and member answers side by side; a "Why Veyl" comparison with typical RAG pipelines; security guarantees; quickstart with collapsible sections for the offline stand-in and services; the seven-step demo as a table; the screenshot gallery; architecture, query sequence, and data model diagrams (Mermaid); a defence-in-depth table; the read rule written out; test suites with counts; configuration reference (split into what `.env` reaches through Compose and further application settings); LLM providers; operations; development and tech stack; project structure; limitations; documentation index; phase status. Every relative link and anchor checked. Added the MIT licence (user's choice).
**Next** — plan the follow-up work with the user (open review items, hosting a public demo, audit log hardening, streaming answers, more document types).

## 2026-10-18 — Phase 6 complete: demo, security walkthrough, docs

**Summary** — a fresh clone now reaches a working two-company demo with four commands. `seed-demo` creates Acme and Globex with the owner role and everything else through the public API; the spec's walkthrough runs as an end-to-end security test and as a CI smoke step; the dashboard's deferred minors are fixed; GSAP motion is limited to content that arrives after load and is off under reduced motion; architecture, threat model (T1 to T17 mapped to real tests), API, and demo docs; the README leads with the guarantee. A live run with the real model found and fixed a citation bug the stand-in could not reveal.

**Review** — independent whole-phase review: no Critical, 2 Important, 5 Minor; it re-verified the demo live with eight questions, every threat-model reference, and the API docs against the code.

**Fixed**
- *(Important) The no-key instructions broke every question.* Starting with the stand-in overlay and then seeding with a plain `docker compose run` recreated the API without the overlay (pointing at Groq with no key). The README and demo now name both files on every command; the "second copy" tip uses `-p` on every command instead of bash-only syntax.
- *(Important) The audit-log claim overstated.* "Not even the database owner can edit it" was false: the owner can disable its own table's trigger. README, threat model (T8 and limitation 5), and architecture now say exactly what holds, and T8 cites the right test (`test_audit_schema.py::test_even_the_owner_cannot_rewrite_the_audit_log`).
- *(Re-graded to Important) The sign-in levels flashed.* They are server-rendered, so animating them on hydration hid visible content and faded it back. They are no longer animated (D-052). Test RED to GREEN.
- *(Re-graded to Important) The quickstart did not work in Windows PowerShell 5.1 or cmd* (`&&`, bash-only env syntax, quotes kept by cmd). Commands are now one per line, the demo password needs no quotes, and cmd's `copy` is noted.

**Deferred (Minor)**
1. A seed that fails after creating the tenants reports only the raw error, and a rerun then says "already seeded"; it should say the demo is incomplete and to run `docker compose down -v`. A `--wait` timeout is reported as an error although the data is complete, and only the first failed document is named.
2. The citation marker pattern lets a dagger reference run across newlines (`[S1† ... ]` can swallow answer text) and is quadratic on pathological input (bounded by the model's token limit); limit it to `†[^close
]{0,40}`.
3. `test_admin_gets_cited_answer_from_board_financials` passes vacuously when no restricted citation is present; assert the list is non-empty.

**Tests** — 319 backend, 56 frontend. CI green on every Phase 6 commit.
**Next** — the project's planned phases are complete. Open items are listed in [threat-model.md](threat-model.md#open-review-items).

## 2026-10-18 — Phase 6, Task 7: fresh clone and live walkthrough found a citation bug

**Fresh clone** — cloned from GitHub into a folder of another name and followed the README: `cp .env.example .env`, new passwords, LLM key, `docker compose up --build -d`, `docker compose run --rm seed`. It built, migrated, seeded, and processed every document. Note: `docker-compose.yml` pins `name: veyl`, so a second checkout shares the first one's Compose project (and its volumes); the check ran with `COMPOSE_PROJECT_NAME=veyl-fresh`, and the README now says how to run a second copy.

**Live walkthrough with Groq (`openai/gpt-oss-120b`)** — every permission outcome matched the demo: Mia never got the financials, Gary saw no documents and no Acme answers, the roadmap reached Mia after the reclassification and disappeared after the revert, Mark's review reached Mark and not Mia, the audit log held Mia's eight questions with counts and no text.

**Bug found and fixed** — Alice's and Mark's answers lost their citations: the model wrote markers as CJK lenticular brackets (`【S1】`, sometimes `【S2†L3-L5】`), which the server did not recognise, so it removed them and returned no source cards. CI could not catch it because the stand-in always writes `[S1]`. The parser now reads `[S1]`, `[S1, S3]`, fullwidth brackets, and lenticular brackets with an optional line reference, and stores `[S#]`. Test RED to GREEN; 319 backend tests.

**Screenshots** — README screenshots retaken from the demo with the real model (admin and member asking the same revenue question, documents, permissions, people, audit log). The screenshot script now asks the browser for reduced motion, so captures show the settled page rather than a frame of an animation (the first capture caught the sign-in levels half faded in).

**Docs correction** — Mia's revenue question does reach the model: her readable IT FAQ mentions "the Q3 financials", so it clears the relevance threshold; the model finds no figures. `demo.md` step 1 now says so.

## 2026-10-18 — Phase 6, Task 6: documentation

**Done** — `docs/architecture.md` (components and trust boundaries, identity per request, where each permission check lives, ingestion and query pipelines, data model summary, code map); `docs/threat-model.md` (assets, actors, boundaries, T1 to T17 each mapped to real test files and test names, verified by a script that every referenced file and test exists; known limitations; open review items); `docs/api.md` (every endpoint, who may call it, curl examples with responses captured from a running stack, error codes, audit event types); `docs/demo.md` (seeding, who can read what, a seven-step walkthrough with expected results, the same checks with curl, reset). README rewritten to lead with the guarantee and a four-command quickstart with the demo; the long technical sections moved into the docs (details only the README held were added to `architecture.md` first).
**Next** — fresh-clone check, live walkthrough with the real model, demo screenshots, final review (Task 7).

## 2026-10-18 — Phase 6, Task 5: motion

**Done** — `useReveal` (GSAP `useGSAP` + `gsap.matchMedia`): the sign-in panel's four levels appear in order, a newly arrived answer's source cards slide in one after another (reopened history does not animate), and the clearance meter fills once. 350 ms tweens that clear their inline styles; nothing at all under `prefers-reduced-motion: reduce` or without `matchMedia`. Verified in Chromium: with motion, items start transparent and settle at full opacity with no leftover styles; with reduced motion everything is visible on the first frame and no transform is applied. Lenis considered and not used for app screens (D-051). Decisions D-050, D-051. 55 frontend tests.
**Next** — documentation (Task 6).

## 2026-10-18 — Phase 6, Task 4: dashboard polish

**Done** (each with a test that failed first) — the eight user-facing minors deferred from the Phase 5 review:
1. Any 401 after the page has loaded (session revoked, user deactivated, expired) sends the user to `/login?next=<current page>`; the sign-in request itself is excluded.
2. Cancel resets the upload form, including the file input (the form remounts).
3. Chat keeps the question on screen until its new conversation has loaded (no empty-state flash), and conversation load errors are shown.
4. Source markers inside bold text become links; numbered lists stay numbered.
5. Enter during IME composition does not send.
6. Deactivating a person asks for confirmation.
7. Audit date filters cover whole local days.
8. The permission editor shows grants to people no longer in the directory as "Former or inactive person", so they can be removed.

The session handler reads `window.location` when it fires rather than calling `usePathname()`, which Next 16 rejects outside Suspense on prerendered dynamic routes (the build caught it). Test setup gains a minimal `<dialog>` shim (jsdom has none). 53 frontend tests.
**Next** — motion with GSAP (Task 5).

## 2026-10-18 — Phase 6, Task 3: CI demo smoke

**Done** — `docker/smoke/demo.sh`: through the edge, Mia's revenue question never cites the board financials, Alice's does, and Gary sees none of Acme's documents and gets no Acme answer. The Compose smoke job now seeds the demo with `docker compose run --rm seed` and runs it (before the login rate-limit probe). Passed locally against a scratch stack with real embeddings and the LLM stand-in.
**Next** — dashboard polish (Task 4).

## 2026-10-18 — Phase 6, Task 2: the demo walkthrough as a security test

**Done** — `tests/security/test_demo_scenario.py`, seven tests over the seeded demo, asserting on recorded prompts: member cannot reach the board financials; admin gets them with a `restricted` citation; Globex member sees nothing from Acme; reclassifying the roadmap reaches Mia's next question and the revert removes it from the one after; the injected FAQ instruction appears only as source text and the data it asks for is absent; Mark's review reaches Mark and admins only; the audit trail has Mia's questions as references, not text. Markers are chosen so they occur only in documents, never in questions (first draft had a false failure from a marker inside the question).
**Mutation check** — disabling only the clearance rule did not fail any test, because every demo document Mia cannot read is also outside her roles (clearance is a second lock there). Disabling clearance and grants in both the SQL filter and the Python re-check fails four tests; restored. 318 backend tests.
**Next** — CI demo smoke (Task 3).

## 2026-10-18 — Phase 6, Task 1: demo seed

**Done** — `python -m app.cli seed-demo --api-url ...` (Compose service `seed`, profile `demo`): creates Acme and Globex with the owner role, then adds people, uploads the six synthetic documents, and sets permissions **through the public API** as each admin, using a stdlib client (cookies, CSRF header, multipart); waits until every document is processed. Refuses to run twice (exit 1, nothing changed); the password comes only from `VEYL_DEMO_PASSWORD`. The same seed code runs in tests against `TestClient` (session fixture `demo_world`). Checked on a scratch stack: seeded in seconds, second run refused, missing password exit 2. 311 backend tests.
**Next** — the demo walkthrough as a security test (Task 2).

## 2026-10-18 — Phase 6 plan written

**Done** — [Phase 6 plan](superpowers/plans/2026-10-18-phase-6-demo-docs.md): 7 tasks. A `seed-demo` command creates Acme and Globex with the owner role, then adds users, documents, and permissions through the public API as each admin (the same code seeds the tests); the spec's demo walkthrough becomes an end-to-end security test and a CI smoke step; Phase 5 dashboard minors; three GSAP motion moments with reduced motion respected (Lenis considered and not used for app screens); architecture, threat model, API, and demo docs; a fresh-clone check of the README.
**Next** — Task 1 (demo seed).

## 2026-10-17 — Phase 5 review: fixes and deferred items

**Review** — independent whole-phase review: no Critical, 2 Important, 9 Minor. Directory endpoint, citation classification, CSRF handling, rendering of model output (text only), and the proxy matcher were checked and found sound. CI green.

**Fixed**
- *(Important) Open redirect after sign-in.* `/login?next=/%09/evil.example` passed the prefix check; the URL parser drops the tab and the router navigated to `evil.example`. The target is now resolved against the current origin and must keep it (D-048). Tests for `%09`, `%0A`, `%0D`, and `%09%5C` RED to GREEN; verified in Chromium against the running stack.
- *(Important) Arrow keys on a role select changed roles immediately.* Roles now change only on "Save role" (D-049). Test RED to GREEN.
- *(Re-graded to Important) One failed poll blanked the documents list and stopped polling for good.* `useApi` keeps the last good data for the same path after a failed reload and never shows another path's data while loading (this also fixes old conversation messages showing under a newly selected conversation). Tests RED to GREEN.
- *(Re-graded to Important) A failed question was lost.* The composer gives the question back when the request fails (for example, a free-tier rate limit). Test RED to GREEN.

**Deferred (Minor)**
1. A session that ends mid-use shows "Your session has ended" with no link; any 401 should redirect to `/login?next=<path>`.
2. The upload dialog keeps the old file in the file input after Cancel; reset the form on close.
3. Chat: the empty state flashes between sending the first question and the conversation loading; conversation load errors are not shown.
4. Audit date filters use whole UTC days while times display in local time; use local midnight or label as UTC.
5. A source marker inside bold text renders as raw `[S1]`; numbered lists render as bullets.
6. The permission editor cannot show or remove grants to deactivated people (they are not in the directory).
7. Enter submits during IME composition; check `isComposing`.
8. "Deactivate" has no confirmation.
9. Concurrent permission edits: the later save overwrites the earlier one.

**Tests** — 43 frontend, 302 backend.

## 2026-10-17 — Phase 5 complete: dashboard

**Summary** — sign-in, Ask with cited answers and conversations, Documents with clearance-aware upload and sharing, People and Audit log for admins. The UI decides nothing about access; it shows what `/auth/me` hints at and explains API errors in plain words.

**End-to-end check (real model)** — rebuilt the stack without the stand-in, provisioned a local Northwind workspace (admin, manager, two members), uploaded four documents (public, internal, confidential, restricted), and drove the UI with Playwright. The admin's hotel and flight question was answered from *Travel policy* with a citation; the member's question about salary bands got "nothing found" with no model call, and the audit log shows it as 0 passages from 0 documents. The member's document list shows only the public and internal documents.

**Screenshot review found and fixed**
- Answers showed the model's Markdown (`**bold**`) as raw symbols: answers now render a small Markdown subset as text (D-047). Test RED to GREEN.
- Source snippets began with `#`: heading markers are dropped from snippets. Test RED to GREEN.
- The audit detail read "1 passages from 1 documents": correct plurals (`eventDetail`). Test RED to GREEN.
- The documents table rendered empty while loading: it now says "Loading documents".
- On phones, short pages stretched the navigation block: the shell's grid rows are `auto 1fr`.
- The screenshot script captured tables before their rows loaded: it now waits for rows.

**CI** — the Compose smoke job checks `/login` (200), `/chat` without a session (307 to sign-in), and `/chat` with one (200), all through the edge.
**Decisions** — D-043 to D-047. 32 frontend tests, 302 backend tests.
**Next** — whole-phase review, then Phase 6 (demo seed, security docs, README polish).

## 2026-10-17 — Phase 5, Task 6: People and audit log

**Done** — `/admin/users`: add a person (email, temporary password, role), change role, deactivate or reactivate; your own row is read-only. `/admin/audit`: filters for event, person, and date range (whole days in UTC), plain-language event names, outcome, actor email, short detail (passages and documents for questions, reason otherwise), short trace id; keyset pagination with "Show older events". Ruling: the plan loaded the first page from an effect, which the React lint rule rejects (`set-state-in-effect`); the first page now comes from `useApi` keyed on the filter query, and older pages are kept only while they belong to the same query. 28 frontend tests.
**Next** — end-to-end check with the real model, screenshots, docs, review (Task 7).

## 2026-10-17 — Phase 5, Task 5: Documents

**Done** — `/documents` table (classification band and badge, status, date) that polls every 3 seconds only while a document is queued or processing; upload dialog (native `<dialog>`, file, title, classification, roles, people from the directory); `/documents/[id]` with details and, for the uploader and admins, a permission editor and delete. The classification picker offers only levels within the editor's clearance (the API rejects others anyway). Ruling: the editor is keyed on the document id rather than `updated_at`, so the "Permissions saved" notice is not lost when the page reloads the document. 25 frontend tests.
**Next** — People and audit log for admins (Task 6).

## 2026-10-17 — Phase 5, Task 4: Ask page

**Done** — `/chat`: conversation list (open, start new, delete), question composer (Enter to send, Shift+Enter for a new line), answers in Source Serif 4 with superscript source numbers linked to source cards that carry the classification band, badge, title (linking to the document), and snippet. Unknown markers such as `[S9]` are dropped. Errors from the API (model unavailable, session ended) are shown in plain words above the composer. 22 frontend tests.
**Next** — Documents: list, upload, permissions (Task 5).

## 2026-10-17 — Phase 5, Task 3: sign-in and app shell

**Done** — `/login` with a slate panel describing the four classification levels next to the form; sign-in errors never say which field was wrong; `safeNext` only accepts same-site paths (tested against `//host`, `/\host`, and absolute URLs). Signed-in route group wraps pages in the session provider and a shell: navigation from permission hints (People and Audit log for admins only), clearance meter, sign out. 19 frontend tests.
**Next** — Ask page with conversations and cited answers (Task 4).

## 2026-10-17 — Phase 5, Task 2: frontend foundations

**Done** — design tokens (cool paper background, slate ink, steel-blue actions, one colour per classification level) and fonts (IBM Plex Sans for the interface, Source Serif 4 for answers); typed same-origin API client that echoes the CSRF cookie and turns error envelopes into `ApiError`; `useApi` loader; plain-language error messages; `SessionProvider` (loads `/auth/me`, redirects to `/login` on 401); classification band, badge, and clearance meter; native form primitives; `proxy.ts` redirect for requests without a session cookie. Test setup now cleans up renders between tests (Vitest globals are off, so Testing Library could not register its own cleanup). 10 frontend tests.
**Next** — sign-in page and app shell (Task 3).

## 2026-10-17 — Phase 5, Task 1: people directory and citation classification

**Done** — `GET /users/directory` (new `user.directory` action for managers and admins; active users in the caller's tenant only) so document owners can pick people to share with; every citation in `/query` and conversation views now carries the chunk's `classification`, so the UI can mark it. 302 backend tests green.
**Next** — frontend foundations (Task 2).

## 2026-10-17 — Phase 5 plan written

**Done** — [Phase 5 plan](superpowers/plans/2026-10-17-phase-5-dashboard.md): 7 tasks. Backend additions (people directory for sharing, classification on citations); frontend foundations (design tokens, IBM Plex Sans and Source Serif 4, typed API client with CSRF, session provider, `proxy.ts` redirect); sign-in and app shell; Ask with cited answers; Documents with clearance-aware sharing; People and audit log for admins; screenshots and review. Design direction: classification bands (public sage, internal blue, confidential amber, restricted oxblood) are the one bold element on a quiet, light interface.
**Next** — execute Task 1.

## 2026-10-16 — Phase 4 review: fixes and deferred items

**Review** — independent whole-phase review: no Critical, 3 Important, 8 Minor. No path found that sends unauthorized content to the model or the user for cited content.

**Fixed (Important)**
- *Answers built from a revoked source survived if the model did not cite it.* Assistant messages now store every chunk given to the model (`source_chunk_ids`, migration 0006); views and history require all of them to be readable (D-040). Test RED to GREEN.
- *The LLM client forwarded the API key across redirects.* urllib follows a 302 with the `Authorization` header; the reviewer saw the key arrive at a second server. Redirects are now refused (D-041). Test RED to GREEN (the second server receives nothing).
- *Stored answers lost which `[S#]` meant which chunk.* Labels are renumbered in order of first appearance and conversation views return the same citation list as `/query` (D-042). Test RED to GREEN.

**Deferred (Minor)**
1. Deeply nested provider JSON raises `RecursionError` (500, not audited); treat any non-app exception from the LLM as `llm_unavailable`.
2. Delimiter neutralisation misses zero-width, fullwidth, and entity-encoded lookalikes (impact limited to mis-attribution among readable sources); add NFKC and zero-width stripping.
3. Old `[S#]` markers in replayed history can be repeated by the model and resolve to new sources; strip them from history.
4. The `.env.example` `AUDIT_HMAC_KEY` placeholder passes validation; reject it when `ENVIRONMENT=production`.
5. The LLM timeout is per socket operation, not a total deadline (a trickling provider can hold a worker).
6. A conversation deleted mid-request causes a 500 after the model call; re-check ownership under a lock when storing.
7. Test gaps: no assertion for `rag.query` with `result_count=0`; the end-to-end cache test would pass without a cache.
8. `rag.denied` details omit the attempted conversation id.

## 2026-10-16 — Phase 4 complete: answers, citations, conversations, audit

**Summary** — `POST /query` answers only from chunks the caller may read: re-authorized history, permission-filtered retrieval with a full Python re-check, the model skipped when nothing authorized is relevant, untrusted-source prompt framing, server-verified citations, and fail-closed errors. Conversations are owner-only and hide answers whose sources were revoked. The append-only audit log (trigger binds even the owner) records logins, user/document/tenant changes, ingestion, every question (ids, model, trace; question hashed by default), and every read of the log; admins query it with filters and keyset pagination. CI now runs a full question through the stack with a stdlib LLM stand-in (`docker-compose.stub.yml`).

**Live check** — with the user's Groq key, `openai/gpt-oss-120b` answered "Employees are allowed to book economy flights. [S1]" citing *Travel policy*; an unrelated question got "nothing found" without a model call; all three questions appear in the audit log with the question hashed. 298 backend tests.

**Next** — whole-phase review, then Phase 5 (dashboard).

## 2026-10-16 — Phase 4, Task 7: RAG pipeline and /query

**Done** — `app/rag/service.py` (`answer_question`: ownership check with audited denial, re-authorized history, filtered retrieval with no fallback, LLM skipped when nothing authorized is relevant, server-side citations with snippets, one transaction to store the exchange and audit `rag.query`; failures audited as `error` with the stage), `app/conversations` (own conversations only; answers whose cited chunks are no longer readable are hidden in views and history), routers for `/query` and `/conversations`, `create_app(embedder=, llm=)` injection with a lazy real embedder by default. Security tests: brief 3 (canary never in any prompt), 7 (end-to-end cache), 9 (only supplied, readable citations), T12 (injected text stays in its neutralised source block), T13 (history re-authorization; other users' conversations 404), T14 (LLM and embedding failures fail closed and are audited), audit records ids not content, plaintext mode, forged tenant field rejected. Found and fixed a history-ordering bug (per-transaction `now()`). Mutation check: not hiding stale answers fails the history test. 298 tests green.
**Next** — LLM stand-in, end-to-end query smoke, live Groq check, docs, review (Task 8).

## 2026-10-16 — Phase 4, Task 6: retrieval hardening

**Done** — the Python re-check now asserts all five rules, including document `ready`, not deleted, and matching `acl_version` (Phase 3 review minor 1); a test with a SQL filter that ignores document status proves the re-check catches it. Cache key includes `top_k` and `min_score` (minor 5). `fetch_readable_chunks(conn, ctx, ids)` returns the readable subset in input order (used by cache hits, history, and conversation views). `LazyEmbedder` loads the model on first use so the API starts fast. 279 tests green.
**Next** — conversations, RAG pipeline, `/query` (Task 7).

## 2026-10-16 — Phase 4, Task 5: prompt and citations

**Done** — `app/llm/prompt.py`: system prompt (sources are untrusted data; cite with supplied labels; say when not found), labelled `<source>` blocks with neutralised delimiters in content and titles, history between system and question. `app/llm/citations.py`: keeps only supplied labels (case-insensitive, deduplicated, ordered) and strips unknown markers. Mutation check: disabling neutralisation fails 2 tests. 276 tests green.
**Next** — retrieval hardening (Task 6).

## 2026-10-16 — Phase 4, Task 4: LLM client

**Done** — `app/llm/client.py`: `OpenAICompatibleLLM` over stdlib urllib (no new dependency): http(s) URLs only, bearer key only when set, temperature 0.1, bounded response size, `message.content` only (provider `reasoning` ignored); HTTP errors, timeouts, unreachable hosts, invalid JSON, and unexpected shapes all raise `LLMError`. Tested against a real local HTTP server imitating a provider (success, no-key, 401, garbage, wrong shape, slow, unreachable, bad schemes). `RecordingLLM` test fake records every prompt. 268 tests green.
**Next** — prompt builder and citation resolver (Task 5).

## 2026-10-16 — Phase 4, Task 3: audit the existing flows

**Done** — audit events in the same transaction as the action: `auth.login_succeeded`, `auth.login_failed` (deny; only when the account exists, so the tenant is known; addresses D-019), `user.created`/`role_changed`/`activated`/`deactivated`, `document.uploaded`/`acl_changed`/`deleted`, `document.ingested`, `document.ingest_failed` (error), `tenant.updated`. `/tenant` exposes and (admin) updates `audit_query_mode`. A test plants a canary string in a document and proves no audit row contains it. 258 tests green.
**Next** — LLM client (Task 4).

## 2026-10-16 — Phase 4, Task 2: audit service and API

**Done** — `app/audit/service.py`: fixed event-type allowlist, `record()` (ids and decisions only), keyed and normalised `query_reference` (HMAC-SHA256), `list_events` with user/document/type/time filters and keyset pagination (opaque cursor; malformed cursors give 422), and reading the log is itself audited (`audit.viewed`). `GET /api/v1/audit-events` is admin-only and scoped to the caller's tenant (client tenant parameters ignored). `TraceId` dependency added. Brief test 8 covered. 254 tests green.
**Next** — audit the existing flows (Task 3).

## 2026-10-16 — Phase 4, Task 1: schema and settings

**Done** — migration 0005: `conversations`, `messages` (cited chunk/document ids, trace id), `audit_events` (namespaced event types, decision enum, ids not content, JSONB details) with forced RLS and insert/select-only grants; an `audit_events_immutable` trigger blocks UPDATE, DELETE, and TRUNCATE even for the owner; `tenants.audit_query_mode` (hash by default). Settings: LLM endpoint/model/key/timeout/max tokens, history turns, required `AUDIT_HMAC_KEY` (32+ chars), `RETRIEVAL_MIN_SCORE` default 0.5. Compose passes `AUDIT_HMAC_KEY` to api and worker. 246 tests green.
**Next** — audit service and admin API (Task 2).

## 2026-10-16 — Phase 4 plan written; Groq key verified

**Done** — user added a free Groq key to `.env` (git-ignored; verified by length and prefix only, never printed). A models-list request showed `llama-3.3-70b-versatile` is no longer offered; switched the default to `openai/gpt-oss-120b`, which answered a test question in 0.6 s with a correct `[S1]` citation (its separate `reasoning` field will be ignored). Measured bge-small similarity to pick `RETRIEVAL_MIN_SCORE=0.5` (relevant top scores 0.60-0.78, unrelated at most 0.45). Wrote the [Phase 4 plan](superpowers/plans/2026-10-16-phase-4-llm-citations-audit.md): audit schema with an append-only trigger, audit service and admin API, audit of existing flows, OpenAI-compatible client, untrusted-source prompt builder and citation checker, retrieval re-check of every rule, `/query` with re-authorized history, CI smoke through an LLM stand-in.
**Next** — execute Task 1.

## 2026-10-16 — Phase 3 review: fixes and deferred items

**Review** — independent whole-phase review: no Critical, 2 Important, 9 Minor. No path found that returns another tenant's content or content above clearance; all five Review Focus items held (the reviewer streamed 196 MB with no Content-Length and got 413 in 0.2 s).

**Fixed (Important)**
- *A tiny PDF could stall the single worker for hours, and a crashing job was reclaimed forever.* Measured: a 6 KB PDF took 7.9 s in pypdf (quadratic). Extraction now runs in a spawned child process with a wall-clock limit, an address-space limit on Linux, and an extracted-text cap (D-030); tests RED to GREEN. Migration 0004 makes the claim function fail stale jobs that used every attempt, along with their document (D-031); test RED to GREEN.
- *New documents were hidden by cached empty results for up to 10 minutes.* Ingestion now bumps `policy_version` when a document becomes ready (D-032); test RED to GREEN.

**Deferred (Minor)**
1. The Python re-check does not assert document status, `deleted_at`, or `acl_version` (rules 2-3 are SQL-only). Phase 4 prompt assembly should re-check them.
2. NUL bytes or lone surrogates in PDF text are treated as transient (3 retries before failing).
3. A NUL in the upload title returns 500 instead of 422.
4. Two tests would pass without their guards (deleted-before-processing; ingestion race is sequential).
5. The cache key omits `top_k` and `min_score`; add both before Phase 4 exposes them.
6. No lease token on jobs: double processing is possible with several workers or runs over 10 minutes.
7. The 64 KB multipart allowance can be exceeded by about 500 `allowed_user_ids`; cap the list on upload.
8. The baked embedding model revision is not pinned.
9. Chunk ACL equality with the document ACL is not enforced by a database trigger.

**Phase 4 notes from review** — reuse `_fetch_by_ids` to re-authorize conversation history; re-ingestion replaces chunk ids, so old citations will read as "access changed".

## 2026-10-16 — Phase 3 complete: documents, ingestion, filtered retrieval

**Summary** — documents with ACLs and soft delete; chunks carrying a copy of the ACL plus a 384-dim embedding under an HNSW index; an ingestion worker (claim with SKIP LOCKED, prepare outside transactions, persist under FOR SHARE, retries); permission-filtered retrieval with the filter inside the vector query, an ids-only cache keyed by `policy_version`, and a Python re-check that fails closed. SQL and Python read rules proven equal over an exhaustive ACL universe. Uploads validated by type and content, size-limited while streaming, stored under server keys. The embedding model is baked into the image (offline). Compose adds the `worker` service and an `uploads` volume; CI now uploads a document through the public entry point and waits for the real model to ingest it. Verified locally end to end. 229 backend tests.

**Findings during the phase** — SQLAlchemy 2.1 deprecates `ARRAY.any()` (switched to `any_()`); a claim test had a cross-tenant job (fixed); EXPLAIN showed small tenants are searched exactly, and a behavioural recall test for large filtered tenants was flaky because HNSW is approximate, so recall there is documented as best-effort (D-028).

**Open notes** — Next.js may cap proxied request bodies near 10 MB; check large uploads through the proxy in Phase 5. Phase 0 and Phase 2 deferred minors remain listed above.

**Next** — whole-phase review, then Phase 4 (LLM, prompt construction, citations, conversations, audit log).

## 2026-10-16 — Phase 3, Task 7: filtered retrieval and cache

**Done** — `app/retrieval/search.py`: one tenant transaction reads `policy_version`, the cache (chunk ids only, keyed by tenant/user/policy_version/model/normalised query), and runs the pgvector search with `chunk_read_filter` inside the query; cache hits are re-fetched through the same filter; every row is re-checked with `can_read_chunk` and any mismatch raises `AuthorizationMismatch`. Security tests for brief items 1, 2, 6, 7 plus forged cache rows, permission-change invalidation, Python re-check, and fail-closed embedding errors. Mutation checks: removing the SQL filter on cache hits fails the forged-cache test; removing the iterative-scan setting fails its test.
**Finding** — EXPLAIN showed the planner pre-filters small tenants through the tenant btree index (exact search). A behavioural recall test for large, heavily filtered tenants was flaky because HNSW is approximate, so it was replaced by a deterministic test that the iterative-scan setting is applied before the vector query; recall in that case is documented as best-effort. 229 tests green.
**Next** — containers, end-to-end ingestion smoke, docs, review (Task 8).

## 2026-10-16 — Phase 3, Task 6: ingestion worker

**Done** — `app/ingestion/pipeline.py`: `claim_job` (definer function), `Ingestor.prepare` (marks processing; extract/chunk/embed outside the transaction), `persist` (FOR SHARE on the document, replace chunks with the current ACL and `acl_version`, mark ready), `fail` (extraction errors are permanent with a safe message; others retry with backoff up to 3 attempts, then fail with a generic message), `run_once`. `app/worker.py`: loop with SIGTERM/SIGINT handling and DB-outage tolerance. Tests: text and PDF ingestion with ACL copies, encrypted PDF fails permanently, retries then failure, permission change between prepare and persist reaches the chunks, deleted documents skipped. 215 tests green.
**Next** — permission-filtered retrieval and cache (Task 7).

## 2026-10-16 — Phase 3, Task 5: documents API

**Done** — `app/documents/service.py` + router: upload (authorize before storing; uploader always in `allowed_user_ids`; grants only to users in the same tenant; file removed if the DB transaction fails; job queued in the same transaction), list/get via `document_read_filter` (404 for unreadable or foreign ids), permission change (spec 5.4: document ACL + every chunk ACL + `acl_version` + tenant `policy_version` in one transaction under `FOR NO KEY UPDATE`; managers limited to their own uploads and their clearance, before and after), delete (soft delete, chunks and file removed, policy bump). ACL fields returned only to callers who can manage. Body-size middleware mounted on `/documents`. Tests cover roles/clearance on upload, cross-tenant grants, ACL hiding, 404s, chunk ACL rewrite, manager limits, delete, oversize (no file, no row), disallowed types, hostile filenames, forged tenant fields. 209 tests green.
**Next** — ingestion worker (Task 6).

## 2026-10-16 — Phase 3, Task 4: storage and upload hardening

**Done** — `LocalStorage` (keys must be server-generated `uuid/uuid`; atomic write), `validate_upload` (extension allowlist + content sniff, UTF-8/NUL checks, size, SHA-256, safe display basename), `BodySizeLimit` pure ASGI middleware (rejects by declared length or while streaming; replaces whatever the framework answers once the limit is crossed; mutation check: removing the byte count fails the chunked-upload test). 191 tests green.
**Next** — documents API (Task 5).

## 2026-10-16 — Phase 3, Task 3: embeddings, extraction, chunking

**Done** — `app/retrieval/embeddings.py` (`Embedder` protocol, `FastEmbedEmbedder` with dimension check), `app/ingestion/extract.py` (pypdf with encrypted/page-cap/garbage rejection; UTF-8 text with NUL rejection; `ExtractionError` messages safe for users), `app/ingestion/chunker.py` (paragraph/sentence/hard-cut splitter, heading-aware, word-boundary overlap; Hypothesis property: chunks non-empty, bounded, every word covered). `tests/fakes.py`: deterministic hashed embedder, hand-built PDFs, pypdf encryption. The real model test passed locally (related text scores higher). 168 tests green.
**Next** — storage, upload validation, streamed body limit (Task 4).

## 2026-10-16 — Phase 3, Task 2: SQL read filters

**Done** — `app/authz/sql.py`: `document_read_filter(ctx)` and `chunk_read_filter(ctx)` (all five conditions of the chunk rule, over `CHUNK_JOIN`). Exhaustive equivalence test against `can_read_chunk`: 27 callers x 256 ACLs, for chunks and documents; plus unready/deleted/stale-version documents hide their chunks. Mutation check: dropping the clearance condition from SQL fails the proof. Uses `any_()` (SQLAlchemy 2.1 deprecates `ARRAY.any()`). 152 tests green.
**Next** — embeddings, extraction, chunker (Task 3).

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
