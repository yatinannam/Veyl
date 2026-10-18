# Threat model

What Veyl protects, from whom, how, and which tests prove it. Test paths are relative to `backend/tests/` unless they start with `frontend/`; every test named here exists and runs in CI.

## Assets

- **Document text** (chunks, snippets, citations, answers derived from them), by classification: public, internal, confidential, restricted.
- **Embeddings**, which can leak approximate content.
- **Identity**: sessions, password hashes, roles.
- **The audit log**: who asked what, which documents were used, permission changes.
- **The LLM API key**.

## Actors

- A **member, manager, or admin** of a tenant trying to read beyond their clearance or grants.
- A user of **another tenant**.
- An **unauthenticated** client, including one forging headers or cookies from another origin.
- A **document author** planting instructions for the model (prompt injection).
- A **compromised or misbehaving dependency**: the LLM provider, the embedding step, the database being unavailable.

## Trust boundaries

Browser to edge (public); edge to web to API (internal network); API and worker to Postgres (`veyl_app`, RLS); API to the LLM provider (third party, receives only authorized excerpts); operator to Postgres (`veyl_owner`, migrations and tenant provisioning only).

## Threats, mitigations, tests

| # | Threat | Mitigations | Tests |
|---|---|---|---|
| T1 | Cross-tenant data leakage | RLS on every table with transaction-local tenant; explicit tenant filter; composite foreign keys; foreign ids return 404 | `security/test_rls_isolation.py` (`test_no_tenant_context_sees_no_rows`, `test_tenant_context_sees_only_own_rows`, `test_cannot_insert_rows_into_another_tenant`, `test_tenant_context_does_not_leak_into_the_next_transaction`, `test_malformed_tenant_setting_fails_closed`); `security/test_tenant_isolation.py`; `security/test_retrieval_security.py::test_tenant_a_cannot_retrieve_tenant_b_documents`; `security/test_demo_scenario.py::test_other_tenant_sees_nothing_from_acme` |
| T2 | Reading documents above clearance or without a grant | Read rule in the SQL filter plus a Python re-check of every row | `security/test_retrieval_security.py` (`test_member_cannot_retrieve_admin_only_chunks`, `test_clearance_caps_explicit_grants`, `test_python_recheck_fails_closed_if_the_sql_filter_is_wrong`); `security/test_filter_equivalence.py` (property test: SQL filter equals Python rule); `security/test_demo_scenario.py` (`test_member_cannot_reach_board_financials`, `test_personal_share_is_only_for_mark`) |
| T3 | Unauthorized text in the model's context | Prompt built only from re-checked rows; nothing sent when nothing is authorized | `security/test_rag_security.py` (`test_unauthorized_chunks_never_reach_the_llm`, `test_no_authorized_sources_means_no_llm_call`); the demo scenario tests assert on every recorded prompt |
| T4 | Forged tenant or user ids | Identity only from the session; request models forbid extra fields; no tenant fields in schemas | `security/test_forged_identifiers.py`; `security/test_upload_validation.py::test_forged_tenant_field_is_ignored`; `security/test_rag_security.py::test_forged_tenant_in_query_is_rejected` |
| T5 | Missing or invalid authentication | 401 for missing, expired, revoked, tampered sessions and deactivated users; RLS returns nothing with no tenant set | `security/test_fail_closed.py` (`test_missing_or_invalid_session_is_denied`, `test_dead_sessions_are_denied`, `test_deactivated_users_lose_access_on_next_request`); `frontend/src/components/session.test.tsx` |
| T6 | Stale permissions after a change | Chunk ACLs rewritten in the same transaction; `acl_version` match; `policy_version` bump | `security/test_retrieval_security.py` (`test_permission_change_applies_to_the_next_retrieval`, `test_stale_chunks_are_never_returned`); `security/test_demo_scenario.py::test_reclassification_takes_effect_on_next_query_and_revert_removes_it` |
| T7 | Cache leakage | Cache stores chunk ids only, keyed by tenant, user, policy version, model, query, result shape; ids re-filtered on every hit | `security/test_retrieval_security.py` (`test_cached_results_do_not_leak_to_a_less_privileged_user`, `test_forged_cache_entry_is_filtered_on_hit`, `test_permission_change_invalidates_cached_results`); `security/test_rag_security.py::test_cached_retrieval_never_crosses_users_end_to_end` |
| T8 | Reading or tampering with the audit log | Admin-only, tenant-scoped endpoint; reads are themselves audited; database grants plus a trigger that blocks UPDATE, DELETE, TRUNCATE even for the owner | `security/test_audit_access.py` (`test_non_admins_cannot_read_the_audit_log`, `test_admins_see_only_their_own_tenant`, `test_reading_the_audit_log_is_itself_audited`); `integration/test_audit_trail.py` |
| T9 | Citations to documents the reader cannot see | Citations built from authorized rows; markers that do not map to a supplied source are dropped | `security/test_rag_security.py::test_citations_only_reference_supplied_authorized_sources`; `frontend/src/components/chat/answer.test.tsx` |
| T10 | Role escalation | Central `authorize()`; managers capped at their clearance and their own documents; nobody changes their own role or status; role changes in the UI need an explicit save | `security/test_privilege_escalation.py`; `frontend/src/components/people/role-control.test.tsx` |
| T11 | Hostile uploads | Size cap before storage; extension plus magic-byte check; server-generated storage keys; encrypted PDFs rejected; page cap; extraction in a child process with timeout, memory limit, and character cap | `security/test_upload_validation.py`; `integration/test_ingestion.py`; `unit/test_extract.py` |
| T12 | Prompt injection in documents | Sources framed as untrusted data, delimiters neutralised, no tools. Access control never depends on the model obeying | `security/test_rag_security.py::test_prompt_injection_stays_inside_an_untrusted_source_block`; `security/test_demo_scenario.py::test_injected_instruction_cannot_widen_access` |
| T13 | Conversation history leakage | Owner-only conversations; an earlier answer is replayed or shown only while every chunk it used is still readable | `security/test_rag_security.py` (`test_history_is_reauthorized_on_every_turn`, `test_other_users_conversations_are_not_found`, `test_uncited_answers_are_also_hidden_after_revocation`) |
| T14 | Failing open when a dependency fails | Embedding, database, and model errors are error responses with an audit record; no fallback path | `security/test_fail_closed.py::test_database_outage_fails_closed`; `security/test_rag_security.py` (`test_llm_failure_fails_closed`, `test_embedding_failure_fails_closed`); `security/test_retrieval_security.py::test_embedding_failure_fails_closed_without_fallback` |
| T15 | Session theft and CSRF | HttpOnly, SameSite=Lax, Secure in production; double-submit CSRF token; tokens stored as hashes; database-clock expiry; no CORS; post-sign-in redirects limited to this origin | `security/test_auth_sessions.py`; `frontend/src/components/login-form.test.tsx` (`safeNext` cases including tab and newline tricks) |
| T16 | Brute-force sign-in | Per-account and per-IP limits on failures; generic errors; argon2id with a cap on concurrent hashing; forged `X-Forwarded-For` overwritten at the edge | `security/test_auth_sessions.py` (`test_login_failures_are_indistinguishable`, `test_account_is_locked_after_repeated_failures`); CI smoke `docker/smoke/login-ip-limit.sh` |
| T17 | LLM API key exposure | Key only in the API's environment; the client refuses redirects (urllib would forward the `Authorization` header) | `integration/test_llm_client.py` |

## Known limitations

Stated in spec section 11 and repeated here:

1. RLS covers the tenant boundary only; role, user, and classification checks live in the SQL filter and the Python re-check.
2. The LLM provider sees the excerpts the asker may read. Free providers are third parties; Ollama keeps everything local. The demo uses synthetic documents.
3. Prompt injection can still distort answers built from content the user may read. It cannot widen access.
4. Side channels (response timing, "nothing found" versus an answer) are not hardened.
5. The audit log is immutable against the application role and the owner role, not against a database superuser or the host.
6. Rate limiting is in-process and assumes a single API replica.
7. No encryption at rest beyond the host disk; the uploads volume is plaintext.
8. Embeddings can leak approximate content if exfiltrated; they are tenant-scoped and protected like chunks.
9. Single node, local deployment; no high availability.
10. The API trusts forwarded headers because only the edge can reach it; a deployment must keep the API unreachable except through the edge.

## Open review items

Minor findings from the phase reviews that are not fixed yet, tracked in the [dev log](devlog.md):

- Deeply nested provider JSON raises `RecursionError` (500, not audited).
- Delimiter neutralisation misses zero-width, fullwidth, and entity-encoded lookalikes (impact limited to mis-attribution among readable sources).
- Old `[S#]` markers in replayed history can be repeated by the model and resolve to new sources.
- The `.env.example` `AUDIT_HMAC_KEY` placeholder is accepted; it should be rejected in production.
- The LLM timeout is per socket operation, not a total deadline.
- A conversation deleted mid-request causes a 500 after the model call.
- `rag.denied` details omit the attempted conversation id.
- Concurrent permission edits: the later save wins without a warning.
- Ingestion and retrieval minors from the Phase 3 review listed in the dev log.
