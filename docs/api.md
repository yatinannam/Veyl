# API

All endpoints live under `/api/v1`. In the Compose stack, call them through the edge at `http://127.0.0.1:3000` (the browser does the same). The interactive OpenAPI page is at `http://127.0.0.1:8000/api/v1/docs`.

Examples below use the seeded demo (see [demo.md](demo.md)) and were captured from a running stack with the LLM stand-in, so ids and the answer text will differ on yours.

## Conventions

**Sessions.** `POST /auth/login` sets two cookies: `veyl_session` (opaque, HttpOnly, SameSite=Lax, Secure in production) and `veyl_csrf` (readable). Keep them in a cookie jar:

```bash
BASE=http://127.0.0.1:3000/api/v1
curl -c jar.txt -H 'Content-Type: application/json' \
  -d '{"tenant_slug":"acme","email":"alice@acme.test","password":"demo-password-123"}' \
  $BASE/auth/login
CSRF=$(awk '$6 == "veyl_csrf" { print $7 }' jar.txt)
```

**CSRF.** Every `POST`, `PUT`, `PATCH`, and `DELETE` except the login itself must send `X-CSRF-Token: $CSRF`. Without it: `403 csrf_failed`.

**Identity comes from the session only.** Request bodies reject unknown fields, so a `tenant_id` in a body is a validation error, and tenant headers or query parameters are ignored.

**Errors** always have this shape, with a server-generated trace id (also in the `X-Trace-Id` response header):

```json
{ "error": { "code": "csrf_failed", "message": "CSRF token missing or invalid.", "trace_id": "62762091-..." } }
```

| Status | Code | Meaning |
|---|---|---|
| 401 | `unauthenticated` | No session, or it expired, was revoked, or the user was deactivated. |
| 401 | `invalid_credentials` | Sign-in failed. The same answer whatever was wrong. |
| 403 | `forbidden` | Signed in, but not allowed to do this. |
| 403 | `csrf_failed` | Missing or wrong `X-CSRF-Token`. |
| 404 | `not_found` | Does not exist, or you may not know it exists (other tenants, other people's conversations, unreadable documents). |
| 409 | `conflict` | For example, an email already in use. |
| 413 | `payload_too_large` | Upload above `MAX_UPLOAD_MB`. |
| 422 | `validation_error` | Body or parameters invalid; `details` lists fields, never the submitted values. |
| 422 | `upload_rejected`, `unknown_users`, `invalid_cursor` | Specific validation failures. |
| 429 | `rate_limited` | Too many failed sign-ins for this account or address. |
| 500 | `authorization_mismatch` | The database returned a row the Python re-check rejected; the request is aborted. |
| 503 | `service_unavailable`, `llm_unavailable` | Database, embedding, or model unavailable. Never answered from a fallback. |

## Authentication

| Method and path | Who | Notes |
|---|---|---|
| `POST /auth/login` | anyone | `{tenant_slug, email, password}`. Returns the same body as `/auth/me` and sets the cookies. |
| `POST /auth/logout` | signed in | Revokes the session. 204. |
| `GET /auth/me` | signed in | Identity and permission hints. |

```bash
curl -b jar.txt $BASE/auth/me
```

```json
{
  "user": { "id": "78f60024-...", "email": "alice@acme.test", "role": "admin" },
  "tenant": { "id": "1696f23d-...", "slug": "acme", "name": "Acme Corp" },
  "permissions": ["audit.read", "document.upload", "rag.query", "tenant.read", "tenant.update", "user.directory", "user.manage"]
}
```

`permissions` only decides what the dashboard shows. Every request is authorized again on the server.

## Tenant

| Method and path | Who | Notes |
|---|---|---|
| `GET /tenant` | signed in | `{id, slug, name, audit_query_mode}` |
| `PATCH /tenant` | admin | `{name?, audit_query_mode?}`; `audit_query_mode` is `hash` (default) or `plaintext`. |

```bash
curl -b jar.txt -X PATCH -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"audit_query_mode":"plaintext"}' $BASE/tenant
```

Tenants themselves are created only by an operator (`python -m app.cli provision-tenant`), never through the API.

## People

| Method and path | Who | Notes |
|---|---|---|
| `GET /users` | admin | Everyone in the tenant: `{id, email, role, is_active, created_at}`. |
| `POST /users` | admin | `{email, password, role}`; password at least 12 characters. 201. |
| `PATCH /users/{id}` | admin | `{role?, is_active?}`. Not on yourself. Takes effect on that person's next request. |
| `GET /users/directory` | manager, admin | Active colleagues `{id, email, role}`, for sharing documents. |

```bash
curl -b jar.txt -X POST -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"email":"nina@acme.test","password":"a-long-temporary-password","role":"member"}' \
  $BASE/users
```

## Documents

| Method and path | Who | Notes |
|---|---|---|
| `POST /documents` | manager, admin | Multipart: `file`, `title`, `classification`, repeated `allowed_roles`, repeated `allowed_user_ids`. PDF, TXT, or Markdown. 202 with status `pending`. Managers cannot classify above confidential. |
| `GET /documents` | signed in | Documents you can read (and ones you manage). |
| `GET /documents/{id}` | readers | Metadata; the access list is included for the uploader and admins (`can_manage`). |
| `PUT /documents/{id}/permissions` | uploader, admin | `{classification, allowed_roles, allowed_user_ids}`. Applies to the very next question anyone asks. |
| `DELETE /documents/{id}` | uploader, admin | Removes the document and its chunks. 204. |

```bash
curl -b jar.txt -H "X-CSRF-Token: $CSRF" \
  -F file=@notes.md -F title='Team notes' -F classification=internal \
  -F allowed_roles=member -F allowed_roles=manager \
  $BASE/documents
```

```json
{
  "id": "a92cf204-...",
  "title": "IT Help FAQ",
  "original_filename": "it-help-faq.md",
  "mime_type": "text/markdown",
  "size_bytes": 583,
  "classification": "internal",
  "status": "ready",
  "error": null,
  "uploaded_by": "78f60024-...",
  "created_at": "2026-10-18T05:26:04.761227Z",
  "updated_at": "2026-10-18T05:26:09.240474Z",
  "can_manage": true,
  "allowed_roles": ["admin", "manager", "member"],
  "allowed_user_ids": ["78f60024-..."]
}
```

The uploader is always kept in `allowed_user_ids`. Status moves from `pending` to `processing` to `ready` (or `failed` with an `error`) as the worker runs.

## Questions and conversations

| Method and path | Who | Notes |
|---|---|---|
| `POST /query` | signed in | `{question, conversation_id?}`. Starts a conversation when no id is given. |
| `GET /conversations` | owner | Your conversations, newest first. |
| `GET /conversations/{id}` | owner | Messages with citations. Answers whose sources you can no longer read are replaced by a notice. Other people's conversations are 404. |
| `DELETE /conversations/{id}` | owner | 204. |

```bash
curl -b jar.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"question":"What was Acme revenue in Q3?"}' $BASE/query
```

```json
{
  "conversation_id": "36f3d5ac-...",
  "message_id": "1cda4998-...",
  "answer": "According to the documents: ... [S1]",
  "citations": [
    {
      "label": "S1",
      "document_id": "8ccfa02d-...",
      "document_title": "Q3 Board Financials",
      "chunk_id": "2527ed9d-...",
      "snippet": "# Q3 Board Financials Prepared for the board of directors. Restricted. ## Results Acme's revenue in Q3 was 18.4 million USD, ...",
      "classification": "restricted"
    }
  ],
  "trace_id": "184a0fce-..."
}
```

Citations are built on the server from the authorized excerpts the model was given; markers in the model's text that do not match one are removed. When nothing you may read is relevant, the answer is "I could not find anything about that in the documents you can access." and the model is not called.

## Audit log

| Method and path | Who | Notes |
|---|---|---|
| `GET /audit-events` | admin | Filters: `event_type`, `user_id`, `document_id`, `from`, `to` (ISO 8601), `cursor`, `limit` (1 to 200, default 50). Newest first; pass `next_cursor` back as `cursor` for older events. Reading the log is itself audited. |

```bash
curl -b jar.txt "$BASE/audit-events?event_type=rag.query&limit=1"
```

```json
{
  "events": [
    {
      "id": "d19afa70-...",
      "occurred_at": "2026-10-18T05:51:33.404049Z",
      "actor_user_id": "78f60024-...",
      "event_type": "rag.query",
      "decision": "allow",
      "query_ref": "61a1dd918aab...",
      "query_text": null,
      "retrieved_chunk_ids": ["2527ed9d-...", "..."],
      "retrieved_document_ids": ["8ccfa02d-...", "1bae4a95-...", "a92cf204-..."],
      "result_count": 6,
      "trace_id": "184a0fce-...",
      "model": "veyl-stub",
      "details": { "response_id": "stub-1", "cited_chunk_ids": ["2527ed9d-..."], "conversation_id": "36f3d5ac-..." }
    }
  ],
  "next_cursor": "MjAyNi0xMC0x..."
}
```

`query_ref` is an HMAC of the normalised question under `AUDIT_HMAC_KEY`, so the same question can be traced without storing it. `query_text` is filled only when the tenant chose `plaintext`.

Event types: `auth.login_succeeded`, `auth.login_failed`, `user.created`, `user.role_changed`, `user.activated`, `user.deactivated`, `tenant.updated`, `document.uploaded`, `document.acl_changed`, `document.deleted`, `document.ingested`, `document.ingest_failed`, `rag.query`, `rag.denied`, `authz.mismatch`, `audit.viewed`.

## Health

| Method and path | Who | Notes |
|---|---|---|
| `GET /healthz` | anyone | The process is up. |
| `GET /readyz` | anyone | The database is reachable with the app role. 503 otherwise. |
