# Phase 5 — Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Next.js dashboard where people sign in, ask questions and read cited answers, upload and share documents with clearance-aware controls, and (admins) manage people and read the audit log — with the server remaining the only authority on every permission.

**Architecture:** Client components talk to the API through the same-origin `/api/v1` proxy with a small typed `api` client (adds the CSRF header from the readable cookie, turns error envelopes into `ApiError`). A `SessionProvider` loads `/auth/me` once and redirects to `/login` on 401; `proxy.ts` redirects requests without a session cookie before rendering. Permission hints from `/auth/me` only decide what to show; every action is re-checked by the API. Two small backend additions: `GET /users/directory` (managers and admins can pick people to share with) and a `classification` field on citations.

**Tech Stack:** Next.js 16.4 (App Router, `proxy.ts`), React 19, TypeScript, Tailwind CSS v4, shadcn/ui Button (Base UI), native form controls, Vitest + Testing Library, Playwright (screenshots only).

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md` (§10 frontend, §7 API)

**Code blocks:** `file=PATH` is the complete file; `edit=PATH` replaces the text between `<<<<<<< OLD` and `=======` (must occur once) with the text up to `>>>>>>> NEW`; `append=PATH` appends to the file.

## Design direction

- **Subject:** a workplace tool for asking questions of internal documents, bounded by clearance. Audience: employees, document owners, admins. Primary job: get a trustworthy, cited answer; secondary: control who can read what.
- **The one bold element:** classification bands. Each level has one colour, following document-marking convention (public sage `#4E8F6B`, internal blue `#3D6FB4`, confidential amber `#B7791F`, restricted oxblood `#A23B4B`). The colour appears as a thin band beside every document row and citation, as the dot in badges, and as a four-segment clearance meter in the header ("Manager can read up to confidential").
- **Palette:** cool paper `#F6F7F9`, white surfaces, slate ink `#1B2430`, steel-blue actions `#2F5D8A`, rules `#D9DEE5`, muted text `#5A6676`.
- **Type:** IBM Plex Sans for the interface (institutional, technical); Source Serif 4 for answers, because they are excerpts of documents.
- **Layout:** left navigation (Ask, Documents, People, Audit log), header with clearance meter, workspace, account. Chat: conversation column, answers with superscript source numbers linked to source cards below each answer. Sign-in: a slate panel that explains the four levels next to the form.
- **Rejected defaults:** dark "security console" theme (cliché, harder to read); identical card grids; all-caps labels; numbered markers on non-sequences; arrows on buttons.
- **Quality floor:** visible focus rings, keyboard-usable controls, responsive down to phone width, colour never the only carrier of meaning (bands always sit next to a text label).

## Global Constraints

- No emojis anywhere (backend test scans the repo). $0. Every commit updates `README.md` and `docs/devlog.md` and is pushed.
- The UI makes no authorization decisions: hiding controls is a convenience; the API is authoritative and its errors are shown in plain words.
- Only same-origin requests to `/api/v1`; no tokens in JavaScript other than reading the CSRF cookie; never store anything in `localStorage`.
- Redirect targets after sign-in must be same-site paths (`/x`, not `//x` or absolute URLs).
- Frontend checks before every commit: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` (in `frontend/`). Backend checks when backend files change.

## Review Focus

1. **Open redirect through `?next=`** (`//evil.example`, `/\evil`, `https://evil.example`) — expect `/chat`. Test: Task 3 `safeNext` cases.
2. **A stale session cookie** (cookie present, session revoked) — expect the app to land on `/login`, not loop. Proxy only redirects when the cookie is missing; the session provider redirects on 401; `/login` never redirects. Test: Task 2 `SessionProvider` 401 test.
3. **A manager offered classifications above their clearance** — expect only allowed levels in the picker (the API would reject the rest anyway). Test: Task 5 `AclFields` test.
4. **Model text with markers that have no citation** (`[S9]`) — expect them dropped, not rendered as broken links. Test: Task 4 `Answer` test.
5. **Long-running ingestion** — expect the documents list to refresh while anything is queued or processing, and stop polling afterwards. Covered in Task 5 by the polling effect keyed on a derived `busy` flag.

---

### Task 1: Backend support — people directory and citation classification

**Files:**
- Modify: `backend/app/authz/policy.py`, `backend/app/users/service.py`, `backend/app/users/router.py`, `backend/app/rag/service.py`, `backend/app/rag/router.py`, `backend/app/conversations/service.py`, `backend/app/conversations/router.py`, `backend/tests/unit/test_authz_policy.py`, `backend/tests/integration/test_auth_api.py`, `backend/tests/integration/test_users_api.py`, `backend/tests/integration/test_query_api.py`, `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces: `Action.USER_DIRECTORY = "user.directory"` (managers, admins); `GET /api/v1/users/directory` → `[{id, email, role}]` of active users in the caller's tenant; `classification` on every citation (`/query` and conversation views).

- [ ] **Step 1: Failing tests**

```python edit=backend/tests/unit/test_authz_policy.py
<<<<<<< OLD
    assert permissions(Role.MANAGER) == ["document.upload", "rag.query", "tenant.read"]
    assert permissions(Role.ADMIN) == [
        "audit.read",
        "document.upload",
        "rag.query",
        "tenant.read",
        "tenant.update",
        "user.manage",
    ]
=======
    assert permissions(Role.MANAGER) == [
        "document.upload",
        "rag.query",
        "tenant.read",
        "user.directory",
    ]
    assert permissions(Role.ADMIN) == [
        "audit.read",
        "document.upload",
        "rag.query",
        "tenant.read",
        "tenant.update",
        "user.directory",
        "user.manage",
    ]
>>>>>>> NEW
```

```python edit=backend/tests/unit/test_authz_policy.py
<<<<<<< OLD
        Action.AUDIT_READ,
        Action.QUERY,
    ):
=======
        Action.AUDIT_READ,
        Action.QUERY,
        Action.USER_DIRECTORY,
    ):
>>>>>>> NEW
```

```python edit=backend/tests/integration/test_auth_api.py
<<<<<<< OLD
    assert body["permissions"] == ["document.upload", "rag.query", "tenant.read"]
=======
    assert body["permissions"] == ["document.upload", "rag.query", "tenant.read", "user.directory"]
>>>>>>> NEW
```

```python append=backend/tests/integration/test_users_api.py


def test_managers_see_the_people_directory(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    manager, manager_email = create_user(owner_engine, a.tenant_id, role=Role.MANAGER)
    member, member_email = create_user(owner_engine, a.tenant_id)
    create_user(owner_engine, a.tenant_id, is_active=False)
    with client_for(database_urls.app) as client:
        login(client, a.slug, manager_email)
        listed = client.get(f"{API}/users/directory").json()
    assert {(e["id"], e["role"]) for e in listed} == {
        (str(a.admin_id), "admin"),
        (str(manager), "manager"),
        (str(member), "member"),
    }
    assert str(b.admin_id) not in {e["id"] for e in listed}
    with client_for(database_urls.app) as client:
        login(client, a.slug, member_email)
        assert client.get(f"{API}/users/directory").status_code == 403
```

```python edit=backend/tests/integration/test_query_api.py
<<<<<<< OLD
    assert body["citations"][0]["snippet"].startswith("Employees may book")
=======
    assert body["citations"][0]["snippet"].startswith("Employees may book")
    assert body["citations"][0]["classification"] == "internal"
>>>>>>> NEW
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_authz_policy.py tests/integration/test_users_api.py tests/integration/test_query_api.py tests/integration/test_auth_api.py -q` → FAIL.

- [ ] **Step 3: Implement**

```python edit=backend/app/authz/policy.py
<<<<<<< OLD
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_MANAGE = "document.manage"
=======
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_MANAGE = "document.manage"
    # See colleagues' names and roles in order to share documents with them.
    USER_DIRECTORY = "user.directory"
>>>>>>> NEW
```

```python edit=backend/app/authz/policy.py
<<<<<<< OLD
    if action in _ADMIN_ONLY:
        return ctx.role is Role.ADMIN
=======
    if action in _ADMIN_ONLY:
        return ctx.role is Role.ADMIN
    if action is Action.USER_DIRECTORY:
        return ctx.role in _UPLOADERS
>>>>>>> NEW
```

```python edit=backend/app/authz/policy.py
<<<<<<< OLD
    if role in _UPLOADERS:
        granted.add(Action.DOCUMENT_UPLOAD)
=======
    if role in _UPLOADERS:
        granted |= {Action.DOCUMENT_UPLOAD, Action.USER_DIRECTORY}
>>>>>>> NEW
```

```python edit=backend/app/users/service.py
<<<<<<< OLD
def list_users(engine: Engine, ctx: AuthContext) -> list[UserRecord]:
=======
@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    id: UUID
    email: str
    role: Role


def directory(engine: Engine, ctx: AuthContext) -> list[DirectoryEntry]:
    """Active colleagues, so document owners can share with specific people."""
    authorize(ctx, Action.USER_DIRECTORY)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(users.c.id, users.c.email, users.c.role)
            .where(users.c.tenant_id == ctx.tenant_id, users.c.is_active)
            .order_by(users.c.email)
        ).all()
    return [DirectoryEntry(r.id, r.email, Role(r.role)) for r in rows]


def list_users(engine: Engine, ctx: AuthContext) -> list[UserRecord]:
>>>>>>> NEW
```

```python edit=backend/app/users/router.py
<<<<<<< OLD
@router.get("")
def list_users(ctx: CurrentUser, engine: EngineDep) -> list[UserOut]:
=======
class DirectoryEntryOut(BaseModel):
    id: UUID
    email: str
    role: Role


@router.get("/directory")
def directory(ctx: CurrentUser, engine: EngineDep) -> list[DirectoryEntryOut]:
    return [
        DirectoryEntryOut(id=e.id, email=e.email, role=e.role)
        for e in service.directory(engine, ctx)
    ]


@router.get("")
def list_users(ctx: CurrentUser, engine: EngineDep) -> list[UserOut]:
>>>>>>> NEW
```

```python edit=backend/app/rag/service.py
<<<<<<< OLD
class Citation:
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str
=======
class Citation:
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str
    classification: str
>>>>>>> NEW
```

```python edit=backend/app/rag/service.py
<<<<<<< OLD
                snippet=make_snippet(s.chunk.content),
            )
=======
                snippet=make_snippet(s.chunk.content),
                classification=s.chunk.acl.classification.value,
            )
>>>>>>> NEW
```

```python edit=backend/app/rag/router.py
<<<<<<< OLD
    chunk_id: UUID
    snippet: str
=======
    chunk_id: UUID
    snippet: str
    classification: str
>>>>>>> NEW
```

```python edit=backend/app/conversations/service.py
<<<<<<< OLD
class StoredCitation:
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str
=======
class StoredCitation:
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str
    classification: str
>>>>>>> NEW
```

```python edit=backend/app/conversations/service.py
<<<<<<< OLD
                    snippet=make_snippet(readable[chunk_id].content),
=======
                    snippet=make_snippet(readable[chunk_id].content),
                    classification=readable[chunk_id].acl.classification.value,
>>>>>>> NEW
```

```python edit=backend/app/conversations/router.py
<<<<<<< OLD
    chunk_id: UUID
    snippet: str
=======
    chunk_id: UUID
    snippet: str
    classification: str
>>>>>>> NEW
```

- [ ] **Step 4: Run backend checks** — all PASS.
- [ ] **Step 5: README (directory endpoint), devlog, commit, push** — `feat(api): people directory for sharing; classification on citations`

---

### Task 2: Frontend foundations — tokens, fonts, API client, session, classification UI

**Files:**
- Create: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/api.test.ts`, `frontend/src/lib/use-api.ts`, `frontend/src/lib/messages.ts`, `frontend/src/components/classification.tsx`, `frontend/src/components/classification.test.tsx`, `frontend/src/components/session.tsx`, `frontend/src/components/session.test.tsx`, `frontend/src/components/form.tsx`, `frontend/src/proxy.ts`
- Modify: `frontend/src/app/globals.css` (append tokens), `frontend/src/app/layout.tsx` (full), `frontend/src/app/page.tsx` (full); Delete: `frontend/src/app/page.test.tsx`

**Interfaces:**
- Produces: types in `@/lib/types` (`Role`, `Classification`, `CLASSIFICATIONS`, `CLEARANCE`, `withinClearance`, `CLASSIFICATION_LABEL`, `ROLE_LABEL`, `Me`, `Citation`, `QueryResponse`, `ConversationSummary`, `StoredMessage`, `ConversationDetail`, `DocumentInfo`, `DocumentStatus`, `UserInfo`, `DirectoryEntry`, `AuditEvent`, `AuditPage`); `api` and `ApiError` in `@/lib/api`; `useApi(path | null)` → `{data, error, loading, reload}`; `describeError(error)`; `ClassificationBand`, `ClassificationBadge`, `ClearanceMeter`; `SessionProvider`, `useSession()` → `{me, can(permission), signOut()}`; form primitives `Input`, `Select`, `Textarea`, `Field`, `Checkbox`, `ErrorText`.

- [ ] **Step 1: Failing tests**

```ts file=frontend/src/lib/api.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

function respond(status: number, body?: unknown) {
  return vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("api", () => {
  beforeEach(() => {
    document.cookie = "veyl_csrf=csrf-token-123; path=/";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "veyl_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("sends the CSRF cookie as a header on state-changing requests", async () => {
    const fetchMock = respond(200, { ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await api.post("/query", { question: "hi" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/query");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-token-123");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
  });

  it("does not send the CSRF header on reads", async () => {
    const fetchMock = respond(200, []);
    vi.stubGlobal("fetch", fetchMock);
    await api.get("/documents");
    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("lets the browser set the multipart boundary for uploads", async () => {
    const fetchMock = respond(202, { id: "d1" });
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.append("title", "Doc");
    await api.post("/documents", form);
    const init = fetchMock.mock.calls[0][1];
    expect(init.body).toBe(form);
    expect(init.headers["Content-Type"]).toBeUndefined();
  });

  it("turns the error envelope into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      respond(403, { error: { code: "forbidden", message: "Nope.", trace_id: "t-1" } }),
    );
    const error = await api.get("/audit-events").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "forbidden", message: "Nope.", traceId: "t-1" });
  });

  it("returns undefined for 204 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.delete("/conversations/c1")).resolves.toBeUndefined();
  });
});
```

```tsx file=frontend/src/components/classification.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClassificationBadge, ClearanceMeter } from "./classification";
import { CLEARANCE, withinClearance } from "@/lib/types";

describe("classification", () => {
  it("labels the level in words, not colour alone", () => {
    render(<ClassificationBadge level="confidential" />);
    expect(screen.getByText("Confidential")).toBeInTheDocument();
  });

  it("describes the clearance meter for assistive technology", () => {
    render(<ClearanceMeter level={CLEARANCE.manager} />);
    expect(screen.getByRole("img", { name: "Clearance: up to Confidential" })).toBeInTheDocument();
  });

  it("matches the server's clearance rule", () => {
    expect(withinClearance("member", "internal")).toBe(true);
    expect(withinClearance("member", "confidential")).toBe(false);
    expect(withinClearance("manager", "restricted")).toBe(false);
    expect(withinClearance("admin", "restricted")).toBe(true);
  });
});
```

```tsx file=frontend/src/components/session.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

function Who() {
  const { me, can } = useSession();
  return (
    <p>
      {me.user.email} {can("audit.read") ? "auditor" : "reader"}
    </p>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

describe("SessionProvider", () => {
  it("provides the signed-in identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          user: { id: "u1", email: "mia@acme.test", role: "member" },
          tenant: { id: "t1", slug: "acme", name: "Acme" },
          permissions: ["rag.query", "tenant.read"],
        }),
      ),
    );
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    expect(await screen.findByText("mia@acme.test reader")).toBeInTheDocument();
  });

  it("sends a stale or missing session to the sign-in page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({ error: { code: "unauthenticated", message: "Authentication required.", trace_id: "t" } }, { status: 401 }),
      ),
    );
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText(/mia/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `pnpm test` → FAIL (modules missing).

- [ ] **Step 3: Implement**

```css append=frontend/src/app/globals.css

/* Veyl tokens. One colour per classification level, following document-marking
   convention; used for bands beside documents and citations and the clearance meter. */
:root {
  --background: #f6f7f9;
  --foreground: #1b2430;
  --card: #ffffff;
  --card-foreground: #1b2430;
  --popover: #ffffff;
  --popover-foreground: #1b2430;
  --primary: #2f5d8a;
  --primary-foreground: #ffffff;
  --secondary: #e9edf2;
  --secondary-foreground: #1b2430;
  --muted: #eef1f4;
  --muted-foreground: #5a6676;
  --accent: #e3ebf3;
  --accent-foreground: #1b2430;
  --destructive: #a23b4b;
  --border: #d9dee5;
  --input: #c4ccd6;
  --ring: #2f5d8a;
  --radius: 0.5rem;
  --class-public: #4e8f6b;
  --class-internal: #3d6fb4;
  --class-confidential: #b7791f;
  --class-restricted: #a23b4b;
}

@theme inline {
  --color-class-public: var(--class-public);
  --color-class-internal: var(--class-internal);
  --color-class-confidential: var(--class-confidential);
  --color-class-restricted: var(--class-restricted);
  --font-serif: var(--font-serif);
}

@media (prefers-reduced-motion: reduce) {
  * {
    transition-duration: 0ms !important;
    animation-duration: 0ms !important;
  }
}
```

```tsx file=frontend/src/app/layout.tsx
import type { Metadata } from "next";
import { IBM_Plex_Sans, Source_Serif_4 } from "next/font/google";

import "./globals.css";

const sans = IBM_Plex_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const serif = Source_Serif_4({
  variable: "--font-serif",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Veyl",
  description: "Answers from your company's documents, limited to what you are allowed to read.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable} h-full antialiased`}>
      <body className="min-h-full bg-background font-sans text-foreground">{children}</body>
    </html>
  );
}
```

```tsx file=frontend/src/app/page.tsx
import { redirect } from "next/navigation";

export default function Home() {
  redirect("/chat");
}
```

```ts file=frontend/src/lib/types.ts
export type Role = "member" | "manager" | "admin";
export type Classification = "public" | "internal" | "confidential" | "restricted";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export const ROLES: Role[] = ["member", "manager", "admin"];
export const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "restricted"];

export const ROLE_LABEL: Record<Role, string> = {
  member: "Member",
  manager: "Manager",
  admin: "Admin",
};

export const CLASSIFICATION_LABEL: Record<Classification, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

/** Highest classification each role may read. Mirrors the server; the server decides. */
export const CLEARANCE: Record<Role, Classification> = {
  member: "internal",
  manager: "confidential",
  admin: "restricted",
};

export function classificationRank(level: Classification): number {
  return CLASSIFICATIONS.indexOf(level);
}

export function withinClearance(role: Role, level: Classification): boolean {
  return classificationRank(level) <= classificationRank(CLEARANCE[role]);
}

export interface Me {
  user: { id: string; email: string; role: Role };
  tenant: { id: string; slug: string; name: string };
  permissions: string[];
}

export interface Citation {
  label: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  snippet: string;
  classification: Classification;
}

export interface QueryResponse {
  conversation_id: string;
  message_id: string;
  answer: string;
  citations: Citation[];
  trace_id: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cited_document_ids: string[];
  citations: Citation[];
  trace_id: string;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[];
}

export interface DocumentInfo {
  id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  classification: Classification;
  status: DocumentStatus;
  error: string | null;
  uploaded_by: string;
  created_at: string;
  updated_at: string;
  can_manage: boolean;
  allowed_roles: Role[] | null;
  allowed_user_ids: string[] | null;
}

export interface UserInfo {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface DirectoryEntry {
  id: string;
  email: string;
  role: Role;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  event_type: string;
  decision: "allow" | "deny" | "error";
  query_ref: string | null;
  query_text: string | null;
  retrieved_chunk_ids: string[];
  retrieved_document_ids: string[];
  result_count: number | null;
  trace_id: string | null;
  model: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage {
  events: AuditEvent[];
  next_cursor: string | null;
}
```

```ts file=frontend/src/lib/api.ts
/** Same-origin client for the Veyl API. The session cookie travels automatically; the
 * readable CSRF cookie is echoed in a header on state-changing requests. */

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  const match = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie("veyl_csrf");
    if (token) headers["X-CSRF-Token"] = token;
  }
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (data as { error?: { code?: string; message?: string; trace_id?: string } } | null)
      ?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `Request failed with status ${response.status}.`,
      error?.trace_id ?? null,
    );
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: (path: string) => request<void>("DELETE", path),
};
```

```ts file=frontend/src/lib/use-api.ts
"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";

interface Result<T> {
  key: string;
  data: T | null;
  error: ApiError | null;
}

function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, "network_error", "Network error.", null);
}

/** Loads `path` (or nothing when null). Keeps the previous data while reloading. */
export function useApi<T>(path: string | null) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);
  const key = path === null ? null : `${path}#${version}`;

  useEffect(() => {
    if (path === null || key === null) return;
    let active = true;
    api.get<T>(path).then(
      (data) => {
        if (active) setResult({ key, data, error: null });
      },
      (error: unknown) => {
        if (active) setResult({ key, data: null, error: asApiError(error) });
      },
    );
    return () => {
      active = false;
    };
  }, [key, path]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const current = result !== null && result.key === key ? result : null;
  return {
    data: path === null ? null : (current?.data ?? result?.data ?? null),
    error: current?.error ?? null,
    loading: key !== null && current === null,
    reload,
  };
}
```

```ts file=frontend/src/lib/messages.ts
import { ApiError } from "./api";

/** Plain-language explanations of API errors: what happened and what to do next. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  switch (error.code) {
    case "invalid_credentials":
      return "That workspace, email, and password don't match.";
    case "rate_limited":
      return "Too many attempts. Wait a few minutes, then try again.";
    case "llm_unavailable":
      return "The answer service is unavailable right now. Try again in a moment.";
    case "service_unavailable":
      return "Veyl can't reach one of its services right now. Try again in a moment.";
    case "forbidden":
      return "You don't have permission to do that.";
    case "not_found":
      return "That item doesn't exist or you no longer have access to it.";
    case "payload_too_large":
      return "That file is larger than the upload limit.";
    case "csrf_failed":
    case "unauthenticated":
      return "Your session has ended. Sign in again.";
    case "network_error":
      return "Veyl can't be reached. Check your connection and try again.";
    default:
      return error.message;
  }
}
```

```tsx file=frontend/src/components/classification.tsx
import { cn } from "cn";

import {
  CLASSIFICATION_LABEL,
  CLASSIFICATIONS,
  classificationRank,
  type Classification,
} from "@/lib/types";

const FILL: Record<Classification, string> = {
  public: "bg-class-public",
  internal: "bg-class-internal",
  confidential: "bg-class-confidential",
  restricted: "bg-class-restricted",
};

/** The vertical band beside a document or citation. Always paired with a text label. */
export function ClassificationBand({
  level,
  className,
}: {
  level: Classification;
  className?: string;
}) {
  return (
    <span aria-hidden className={cn("w-1 shrink-0 self-stretch rounded-full", FILL[level], className)} />
  );
}

export function ClassificationBadge({ level }: { level: Classification }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-foreground">
      <span aria-hidden className={cn("size-2 rounded-full", FILL[level])} />
      {CLASSIFICATION_LABEL[level]}
    </span>
  );
}

/** Four segments, filled up to the reader's clearance. */
export function ClearanceMeter({ level }: { level: Classification }) {
  return (
    <span
      role="img"
      aria-label={`Clearance: up to ${CLASSIFICATION_LABEL[level]}`}
      className="flex items-center gap-0.5"
    >
      {CLASSIFICATIONS.map((step) => (
        <span
          key={step}
          className={cn(
            "h-1.5 w-5 rounded-full",
            classificationRank(step) <= classificationRank(level) ? FILL[step] : "bg-border",
          )}
        />
      ))}
    </span>
  );
}
```

```tsx file=frontend/src/components/session.tsx
"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { Me } from "@/lib/types";
import { useApi } from "@/lib/use-api";

interface Session {
  me: Me;
  can: (permission: string) => boolean;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { data, error } = useApi<Me>("/auth/me");
  const signedOut = error?.status === 401;

  useEffect(() => {
    // A missing or stale session lands on sign-in. /login itself never redirects, so a
    // stale cookie cannot cause a loop.
    if (signedOut) router.replace("/login");
  }, [signedOut, router]);

  if (error && !signedOut) return <Centered>{describeError(error)} Reload the page to try again.</Centered>;
  if (!data) return <Centered>Loading your workspace</Centered>;

  const session: Session = {
    me: data,
    can: (permission) => data.permissions.includes(permission),
    signOut: async () => {
      try {
        await api.post("/auth/logout");
      } finally {
        router.replace("/login");
      }
    },
  };
  return <SessionContext value={session}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
```

```tsx file=frontend/src/components/form.tsx
import { cn } from "cn";
import type { ComponentProps, ReactNode } from "react";

const control =
  "w-full rounded-md border border-input bg-card px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(control, "h-9", className)} {...props} />;
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(control, "h-9 pr-8", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(control, "py-2 leading-relaxed", className)} {...props} />;
}

export function Checkbox({ className, ...props }: ComponentProps<"input">) {
  return <input type="checkbox" className={cn("size-4 accent-primary", className)} {...props} />;
}

export function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {children}
    </p>
  );
}
```

```ts file=frontend/src/proxy.ts
import { NextResponse, type NextRequest } from "next/server";

/** Convenience only: requests without a session cookie go to sign-in before rendering.
 * The API decides every permission; a present-but-stale cookie is handled client-side. */
export function proxy(request: NextRequest) {
  if (request.cookies.has("veyl_session")) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/chat/:path*", "/documents/:path*", "/admin/:path*"],
};
```

Delete `frontend/src/app/page.test.tsx` (the landing page is now a redirect).

- [ ] **Step 4: Run frontend checks** — `pnpm lint && pnpm typecheck && pnpm test && pnpm build` → PASS.
- [ ] **Step 5: README (frontend structure), devlog, commit, push** — `feat(frontend): tokens, typed API client, session, classification components`

---

### Task 3: Sign-in page and app shell

**Files:**
- Create: `frontend/src/components/login-form.tsx`, `frontend/src/components/login-form.test.tsx`, `frontend/src/app/login/page.tsx`, `frontend/src/components/app-shell.tsx`, `frontend/src/components/app-shell.test.tsx`, `frontend/src/app/(app)/layout.tsx`

**Interfaces:**
- Produces: `LoginForm`, `safeNext(search) -> string`; `AppShell`, `navItems(permissions) -> {href, label}[]`; route group `(app)` wrapping every signed-in page in `SessionProvider` + `AppShell`.

- [ ] **Step 1: Failing tests**

```tsx file=frontend/src/components/login-form.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm, safeNext } from "./login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

function fill() {
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "mia@acme.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("safeNext", () => {
  it.each([
    ["?next=/documents", "/documents"],
    ["?next=//evil.example", "/chat"],
    ["?next=/%5Cevil.example", "/chat"],
    ["?next=https://evil.example", "/chat"],
    ["", "/chat"],
  ])("%s -> %s", (search, expected) => {
    expect(safeNext(search)).toBe(expected);
  });
});

describe("LoginForm", () => {
  it("signs in and continues to the app", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    render(<LoginForm />);
    fill();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      tenant_slug: "acme",
      email: "mia@acme.test",
      password: "correct-horse",
    });
  });

  it("explains a failed sign-in without saying which part was wrong", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { error: { code: "invalid_credentials", message: "x", trace_id: "t" } },
          { status: 401 },
        ),
      ),
    );
    render(<LoginForm />);
    fill();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That workspace, email, and password don't match.",
    );
    expect(replace).not.toHaveBeenCalled();
  });
});
```

```ts file=frontend/src/components/app-shell.test.ts
import { describe, expect, it } from "vitest";

import { navItems } from "./app-shell";

describe("navItems", () => {
  it("shows members only what they can use", () => {
    expect(navItems(["rag.query", "tenant.read"]).map((i) => i.label)).toEqual(["Ask", "Documents"]);
  });

  it("adds people and the audit log for admins", () => {
    expect(
      navItems(["rag.query", "tenant.read", "user.manage", "audit.read"]).map((i) => i.label),
    ).toEqual(["Ask", "Documents", "People", "Audit log"]);
  });
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

```tsx file=frontend/src/components/login-form.tsx
"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field, ErrorText, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";

/** Only same-site paths are allowed as a post-sign-in destination. */
export function safeNext(search: string): string {
  const next = new URLSearchParams(search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")) {
    return next;
  }
  return "/chat";
}

export function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/login", {
        tenant_slug: form.get("workspace"),
        email: form.get("email"),
        password: form.get("password"),
      });
      router.replace(safeNext(window.location.search));
    } catch (failure) {
      setError(describeError(failure));
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4">
      <Field label="Workspace" htmlFor="workspace" hint="The short name your admin gave you, such as acme.">
        <Input id="workspace" name="workspace" autoComplete="organization" required />
      </Field>
      <Field label="Email" htmlFor="email">
        <Input id="email" name="email" type="email" autoComplete="username" required />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </Field>
      <ErrorText>{error}</ErrorText>
      <Button type="submit" size="lg" disabled={pending}>
        {pending ? "Signing in" : "Sign in"}
      </Button>
    </form>
  );
}
```

```tsx file=frontend/src/app/login/page.tsx
import { ClassificationBand } from "@/components/classification";
import { LoginForm } from "@/components/login-form";
import { CLASSIFICATION_LABEL, type Classification } from "@/lib/types";

const LEVELS: { level: Classification; readers: string }[] = [
  { level: "restricted", readers: "Admins only" },
  { level: "confidential", readers: "Managers and admins" },
  { level: "internal", readers: "Everyone in the workspace who is granted access" },
  { level: "public", readers: "Anyone granted access, with no sensitive content" },
];

export default function LoginPage() {
  return (
    <main className="grid min-h-screen md:grid-cols-[minmax(0,1fr)_minmax(22rem,30rem)]">
      <section className="hidden flex-col justify-between bg-foreground p-10 text-background md:flex">
        <p className="text-lg font-semibold tracking-tight">Veyl</p>
        <div className="max-w-md space-y-8">
          <p className="font-serif text-3xl leading-snug">
            Answers from your company&apos;s documents, never beyond what you are cleared to read.
          </p>
          <ul aria-label="Classification levels" className="space-y-4">
            {LEVELS.map(({ level, readers }) => (
              <li key={level} className="flex gap-3">
                <ClassificationBand level={level} className="w-1.5" />
                <div>
                  <p className="font-medium">{CLASSIFICATION_LABEL[level]}</p>
                  <p className="text-sm opacity-75">{readers}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="max-w-md text-sm opacity-75">
          Every question is checked against document permissions before any text reaches the
          language model.
        </p>
      </section>
      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm space-y-6">
          <div className="space-y-1">
            <p className="text-lg font-semibold tracking-tight md:hidden">Veyl</p>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="text-sm text-muted-foreground">Use the workspace your admin set up for you.</p>
          </div>
          <LoginForm />
        </div>
      </section>
    </main>
  );
}
```

```tsx file=frontend/src/components/app-shell.tsx
"use client";

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { ClearanceMeter } from "@/components/classification";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { CLASSIFICATION_LABEL, CLEARANCE, ROLE_LABEL } from "@/lib/types";

export interface NavItem {
  href: string;
  label: string;
}

/** Navigation hints from the permission list. The API enforces every page's data. */
export function navItems(permissions: string[]): NavItem[] {
  const items: NavItem[] = [
    { href: "/chat", label: "Ask" },
    { href: "/documents", label: "Documents" },
  ];
  if (permissions.includes("user.manage")) items.push({ href: "/admin/users", label: "People" });
  if (permissions.includes("audit.read")) items.push({ href: "/admin/audit", label: "Audit log" });
  return items;
}

export function AppShell({ children }: { children: ReactNode }) {
  const { me, signOut } = useSession();
  const pathname = usePathname();
  const clearance = CLEARANCE[me.user.role];

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[13rem_minmax(0,1fr)]">
      <aside className="border-b border-border bg-card md:border-r md:border-b-0">
        <div className="px-5 pt-5 pb-3">
          <p className="text-lg font-semibold tracking-tight">Veyl</p>
          <p className="truncate text-sm text-muted-foreground">{me.tenant.name}</p>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col">
          {navItems(me.permissions).map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-6 py-3">
          <div className="flex items-center gap-3">
            <ClearanceMeter level={clearance} />
            <p className="text-sm">
              <span className="font-medium">{ROLE_LABEL[me.user.role]}</span>
              <span className="text-muted-foreground">
                {" "}
                can read up to {CLASSIFICATION_LABEL[clearance].toLowerCase()}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-muted-foreground sm:inline">{me.user.email}</span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
```

```tsx file=frontend/src/app/(app)/layout.tsx
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session";

export default function SignedInLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
```

- [ ] **Step 4: Frontend checks** — PASS. (`/chat` 404s until Task 4; build still succeeds.)
- [ ] **Step 5: README, devlog, commit, push** — `feat(frontend): sign-in page and app shell with clearance meter`

---

### Task 4: Ask — conversations and cited answers

**Files:**
- Create: `frontend/src/components/chat/answer.tsx`, `frontend/src/components/chat/answer.test.tsx`, `frontend/src/components/chat/composer.tsx`, `frontend/src/app/(app)/chat/page.tsx`, `frontend/src/lib/format.ts`

**Interfaces:**
- Produces: `Answer({content, citations, idPrefix})`; `Composer({onAsk, disabled})`; `formatDate`, `formatBytes` in `@/lib/format`.

- [ ] **Step 1: Failing test**

```tsx file=frontend/src/components/chat/answer.test.tsx
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "./answer";
import type { Citation } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const citations: Citation[] = [
  {
    label: "S1",
    document_id: "doc-travel",
    document_title: "Travel policy",
    chunk_id: "c1",
    snippet: "Employees may book economy flights.",
    classification: "internal",
  },
];

describe("Answer", () => {
  it("turns source markers into links to the matching source", () => {
    render(<Answer content="Economy only [S1]." citations={citations} idPrefix="m1" />);
    const marker = screen.getByRole("link", { name: "Source 1: Travel policy" });
    expect(marker).toHaveAttribute("href", "#m1-S1");
    const sources = screen.getByRole("list", { name: "Sources" });
    expect(within(sources).getByRole("link", { name: "Travel policy" })).toHaveAttribute(
      "href",
      "/documents/doc-travel",
    );
    expect(within(sources).getByText("Internal")).toBeInTheDocument();
  });

  it("drops markers that have no citation", () => {
    render(<Answer content="Something [S9] here." citations={citations} idPrefix="m2" />);
    expect(screen.queryByText("[S9]")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Source 9/ })).not.toBeInTheDocument();
  });

  it("shows no source list when nothing was cited", () => {
    render(<Answer content="I could not find that." citations={[]} idPrefix="m3" />);
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

```ts file=frontend/src/lib/format.ts
const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function formatDate(iso: string): string {
  return DATE.format(new Date(iso));
}

export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
```

```tsx file=frontend/src/components/chat/answer.tsx
import Link from "next/link";
import { Fragment } from "react";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import type { Citation } from "@/lib/types";

const MARKER = /^\[(S\d+)\]$/;

/** An answer in document type, with superscript source numbers linked to source cards. */
export function Answer({
  content,
  citations,
  idPrefix,
}: {
  content: string;
  citations: Citation[];
  idPrefix: string;
}) {
  const byLabel = new Map(citations.map((c) => [c.label, c]));
  const parts = content.split(/(\[S\d+\])/g);

  return (
    <div className="space-y-4">
      <p className="font-serif text-[1.0625rem] leading-relaxed whitespace-pre-wrap">
        {parts.map((part, index) => {
          const match = MARKER.exec(part);
          if (!match) return <Fragment key={index}>{part}</Fragment>;
          const citation = byLabel.get(match[1]);
          if (!citation) return null;
          const number = citation.label.slice(1);
          return (
            <sup key={index}>
              <a
                href={`#${idPrefix}-${citation.label}`}
                aria-label={`Source ${number}: ${citation.document_title}`}
                className="ml-0.5 rounded-sm px-0.5 font-sans text-xs font-medium text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                {number}
              </a>
            </sup>
          );
        })}
      </p>
      {citations.length > 0 ? (
        <ul aria-label="Sources" className="space-y-2">
          {citations.map((citation) => (
            <li
              key={citation.label}
              id={`${idPrefix}-${citation.label}`}
              className="flex gap-3 rounded-md border border-border bg-card p-3 scroll-mt-24"
            >
              <ClassificationBand level={citation.classification} />
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    {citation.label.slice(1)}
                  </span>
                  <Link
                    href={`/documents/${citation.document_id}`}
                    className="text-sm font-medium hover:underline"
                  >
                    {citation.document_title}
                  </Link>
                  <ClassificationBadge level={citation.classification} />
                </div>
                <p className="text-sm text-muted-foreground">{citation.snippet}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
```

```tsx file=frontend/src/components/chat/composer.tsx
"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";

import { Textarea } from "@/components/form";
import { Button } from "@/components/ui/button";

export function Composer({ onAsk, disabled }: { onAsk: (question: string) => void; disabled: boolean }) {
  const [question, setQuestion] = useState("");

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || disabled) return;
    onAsk(trimmed);
    setQuestion("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <label htmlFor="question" className="sr-only">
        Your question
      </label>
      <Textarea
        id="question"
        rows={2}
        maxLength={2000}
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about the documents you can read"
        className="min-h-12 resize-y"
      />
      <Button type="submit" size="lg" disabled={disabled || !question.trim()}>
        Ask
      </Button>
    </form>
  );
}
```

```tsx file=frontend/src/app/(app)/chat/page.tsx
"use client";

import { cn } from "cn";
import { useState } from "react";

import { Answer } from "@/components/chat/answer";
import { Composer } from "@/components/chat/composer";
import { ErrorText } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { ConversationDetail, ConversationSummary, QueryResponse } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function AskPage() {
  const conversations = useApi<ConversationSummary[]>("/conversations");
  const [activeId, setActiveId] = useState<string | null>(null);
  const detail = useApi<ConversationDetail>(activeId ? `/conversations/${activeId}` : null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const messages = activeId ? (detail.data?.messages ?? []) : [];

  async function ask(question: string) {
    setPending(question);
    setError(null);
    try {
      const answer = await api.post<QueryResponse>("/query", {
        question,
        conversation_id: activeId ?? undefined,
      });
      if (answer.conversation_id === activeId) detail.reload();
      else setActiveId(answer.conversation_id);
      conversations.reload();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(null);
    }
  }

  async function remove(id: string) {
    try {
      await api.delete(`/conversations/${id}`);
      if (id === activeId) setActiveId(null);
      conversations.reload();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="space-y-3">
        <Button variant="outline" className="w-full" onClick={() => setActiveId(null)}>
          New conversation
        </Button>
        <nav aria-label="Conversations" className="space-y-1">
          {(conversations.data ?? []).map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => setActiveId(conversation.id)}
              aria-current={conversation.id === activeId ? "true" : undefined}
              className={cn(
                "block w-full rounded-md px-2.5 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
                conversation.id === activeId ? "bg-accent" : "hover:bg-muted",
              )}
            >
              <span className="line-clamp-2 text-sm">{conversation.title}</span>
              <span className="text-xs text-muted-foreground">{formatDate(conversation.updated_at)}</span>
            </button>
          ))}
        </nav>
      </aside>

      <section aria-label="Conversation" className="flex min-h-[70vh] flex-col gap-6">
        <div className="flex-1 space-y-8">
          {messages.length === 0 && !pending ? (
            <div className="max-w-xl space-y-2 pt-6">
              <h1 className="text-xl font-semibold tracking-tight">Ask a question</h1>
              <p className="text-sm text-muted-foreground">
                Answers come only from documents you can read, with the sources listed under
                each answer. If nothing you can read covers the question, Veyl says so.
              </p>
            </div>
          ) : null}
          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="max-w-2xl">
                <p className="text-xs font-medium text-muted-foreground">You asked</p>
                <p className="mt-1 text-base font-medium">{message.content}</p>
              </div>
            ) : (
              <article key={message.id} className="max-w-2xl">
                <Answer content={message.content} citations={message.citations} idPrefix={message.id} />
              </article>
            ),
          )}
          {pending ? (
            <div className="max-w-2xl space-y-2" aria-live="polite">
              <p className="text-xs font-medium text-muted-foreground">You asked</p>
              <p className="text-base font-medium">{pending}</p>
              <p className="text-sm text-muted-foreground">Searching the documents you can read</p>
            </div>
          ) : null}
        </div>
        <div className="sticky bottom-0 space-y-2 border-t border-border bg-background pt-4 pb-2">
          <ErrorText>{error}</ErrorText>
          <Composer onAsk={(question) => void ask(question)} disabled={pending !== null} />
          {activeId ? (
            <div className="flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => void remove(activeId)}>
                Delete this conversation
              </Button>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Frontend checks** — PASS.
- [ ] **Step 5: README, devlog, commit, push** — `feat(frontend): ask page with cited answers and conversations`

---

### Task 5: Documents — list, upload, permissions

**Files:**
- Create: `frontend/src/components/documents/acl-fields.tsx`, `frontend/src/components/documents/acl-fields.test.tsx`, `frontend/src/components/documents/upload-dialog.tsx`, `frontend/src/components/documents/permission-editor.tsx`, `frontend/src/components/documents/status.tsx`, `frontend/src/app/(app)/documents/page.tsx`, `frontend/src/app/(app)/documents/[id]/page.tsx`

**Interfaces:**
- Produces: `Acl {classification, roles, userIds}`, `AclFields({value, onChange, role, people})`; `UploadDialog({role, people, onUploaded})`; `PermissionEditor({document, role, people, onSaved})`; `StatusText({document})`.

- [ ] **Step 1: Failing test**

```tsx file=frontend/src/components/documents/acl-fields.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AclFields, type Acl } from "./acl-fields";

const people = [
  { id: "u1", email: "alice@acme.test", role: "admin" as const },
  { id: "u2", email: "mia@acme.test", role: "member" as const },
];
const value: Acl = { classification: "internal", roles: ["member"], userIds: [] };

describe("AclFields", () => {
  it("offers managers only the levels they are cleared for", () => {
    render(<AclFields value={value} onChange={vi.fn()} role="manager" people={people} />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Public", "Internal", "Confidential"]);
  });

  it("offers admins every level", () => {
    render(<AclFields value={value} onChange={vi.fn()} role="admin" people={people} />);
    expect(screen.getAllByRole("option")).toHaveLength(4);
  });

  it("toggles roles and people", () => {
    const onChange = vi.fn();
    render(<AclFields value={value} onChange={onChange} role="admin" people={people} />);
    fireEvent.click(screen.getByLabelText("Managers"));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, roles: ["member", "manager"] });
    fireEvent.click(screen.getByLabelText("mia@acme.test"));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, userIds: ["u2"] });
  });
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

```tsx file=frontend/src/components/documents/acl-fields.tsx
import { Checkbox, Field, Select } from "@/components/form";
import {
  CLASSIFICATION_LABEL,
  CLASSIFICATIONS,
  ROLES,
  withinClearance,
  type Classification,
  type DirectoryEntry,
  type Role,
} from "@/lib/types";

export interface Acl {
  classification: Classification;
  roles: Role[];
  userIds: string[];
}

const ROLE_PLURAL: Record<Role, string> = { member: "Members", manager: "Managers", admin: "Admins" };

function toggle<T>(items: T[], item: T): T[] {
  return items.includes(item) ? items.filter((i) => i !== item) : [...items, item];
}

/** Who may read a document. Levels above the editor's clearance are not offered; the
 * server rejects them regardless. */
export function AclFields({
  value,
  onChange,
  role,
  people,
}: {
  value: Acl;
  onChange: (next: Acl) => void;
  role: Role;
  people: DirectoryEntry[];
}) {
  const levels = CLASSIFICATIONS.filter((level) => withinClearance(role, level));
  return (
    <div className="grid gap-5">
      <Field
        label="Classification"
        htmlFor="classification"
        hint="Readers also need clearance for this level: members up to internal, managers up to confidential."
      >
        <Select
          id="classification"
          value={value.classification}
          onChange={(event) =>
            onChange({ ...value, classification: event.target.value as Classification })
          }
        >
          {levels.map((level) => (
            <option key={level} value={level}>
              {CLASSIFICATION_LABEL[level]}
            </option>
          ))}
        </Select>
      </Field>
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">Roles that can read it</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {ROLES.map((r) => (
            <label key={r} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={value.roles.includes(r)}
                onChange={() => onChange({ ...value, roles: toggle(value.roles, r) })}
              />
              {ROLE_PLURAL[r]}
            </label>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Admins can always read documents in this workspace, and the uploader keeps access.
        </p>
      </fieldset>
      {people.length > 0 ? (
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium">Also share with</legend>
          <div className="max-h-40 space-y-1.5 overflow-y-auto rounded-md border border-border p-2.5">
            {people.map((person) => (
              <label key={person.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={value.userIds.includes(person.id)}
                  onChange={() => onChange({ ...value, userIds: toggle(value.userIds, person.id) })}
                />
                {person.email}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
    </div>
  );
}
```

```tsx file=frontend/src/components/documents/status.tsx
import type { DocumentInfo } from "@/lib/types";

const LABEL = { pending: "Queued", processing: "Processing", ready: "Ready", failed: "Failed" } as const;

export function StatusText({ document }: { document: DocumentInfo }) {
  if (document.status === "failed") {
    return (
      <span className="text-sm text-destructive">
        Failed{document.error ? `: ${document.error}` : ""}
      </span>
    );
  }
  return (
    <span className={document.status === "ready" ? "text-sm" : "text-sm text-muted-foreground"}>
      {LABEL[document.status]}
    </span>
  );
}
```

```tsx file=frontend/src/components/documents/upload-dialog.tsx
"use client";

import { useRef, useState, type FormEvent } from "react";

import { AclFields, type Acl } from "@/components/documents/acl-fields";
import { ErrorText, Field, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo, Role } from "@/lib/types";

const INITIAL_ACL: Acl = { classification: "internal", roles: ["member"], userIds: [] };

export function UploadDialog({
  role,
  people,
  onUploaded,
}: {
  role: Role;
  people: DirectoryEntry[];
  onUploaded: (document: DocumentInfo) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [acl, setAcl] = useState<Acl>(INITIAL_ACL);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function close() {
    dialog.current?.close();
    setFile(null);
    setTitle("");
    setAcl(INITIAL_ACL);
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("title", title.trim() || file.name);
    form.append("classification", acl.classification);
    acl.roles.forEach((r) => form.append("allowed_roles", r));
    acl.userIds.forEach((id) => form.append("allowed_user_ids", id));
    setPending(true);
    setError(null);
    try {
      const document = await api.post<DocumentInfo>("/documents", form);
      onUploaded(document);
      close();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <Button onClick={() => dialog.current?.showModal()}>Upload document</Button>
      <dialog
        ref={dialog}
        aria-labelledby="upload-title"
        className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-foreground/40"
        onClose={close}
      >
        <form onSubmit={submit} className="grid gap-5 p-6">
          <div className="space-y-1">
            <h2 id="upload-title" className="text-lg font-semibold tracking-tight">
              Upload a document
            </h2>
            <p className="text-sm text-muted-foreground">PDF, TXT, or Markdown. It becomes searchable once processing finishes.</p>
          </div>
          <Field label="File" htmlFor="file">
            <Input
              id="file"
              type="file"
              accept=".pdf,.txt,.md"
              required
              className="py-1.5"
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null;
                setFile(chosen);
                if (chosen && !title) setTitle(chosen.name.replace(/\.[^.]+$/, ""));
              }}
            />
          </Field>
          <Field label="Title" htmlFor="title">
            <Input id="title" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} />
          </Field>
          <AclFields value={acl} onChange={setAcl} role={role} people={people} />
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={!file || pending}>
              {pending ? "Uploading" : "Upload"}
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}
```

```tsx file=frontend/src/components/documents/permission-editor.tsx
"use client";

import { useState } from "react";

import { AclFields, type Acl } from "@/components/documents/acl-fields";
import { ErrorText } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo, Role } from "@/lib/types";

export function PermissionEditor({
  document,
  role,
  people,
  onSaved,
  onDeleted,
}: {
  document: DocumentInfo;
  role: Role;
  people: DirectoryEntry[];
  onSaved: (document: DocumentInfo) => void;
  onDeleted: () => void;
}) {
  const [acl, setAcl] = useState<Acl>({
    classification: document.classification,
    roles: document.allowed_roles ?? [],
    userIds: (document.allowed_user_ids ?? []).filter((id) => id !== document.uploaded_by),
  });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function save() {
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.put<DocumentInfo>(`/documents/${document.id}/permissions`, {
        classification: acl.classification,
        allowed_roles: acl.roles,
        allowed_user_ids: acl.userIds,
      });
      onSaved(updated);
      setNotice("Permissions saved. They apply to the next question anyone asks.");
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${document.title}"? People will no longer get answers from it.`)) return;
    try {
      await api.delete(`/documents/${document.id}`);
      onDeleted();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <section aria-labelledby="permissions-title" className="space-y-5 rounded-lg border border-border bg-card p-5">
      <h2 id="permissions-title" className="text-base font-semibold">
        Who can read this
      </h2>
      <AclFields value={acl} onChange={setAcl} role={role} people={people} />
      <ErrorText>{error}</ErrorText>
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-between gap-2">
        <Button variant="destructive" onClick={() => void remove()}>
          Delete document
        </Button>
        <Button onClick={() => void save()} disabled={pending}>
          {pending ? "Saving" : "Save permissions"}
        </Button>
      </div>
    </section>
  );
}
```

```tsx file=frontend/src/app/(app)/documents/page.tsx
"use client";

import Link from "next/link";
import { useEffect } from "react";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import { StatusText } from "@/components/documents/status";
import { UploadDialog } from "@/components/documents/upload-dialog";
import { ErrorText } from "@/components/form";
import { useSession } from "@/components/session";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function DocumentsPage() {
  const { me, can } = useSession();
  const documents = useApi<DocumentInfo[]>("/documents");
  const people = useApi<DirectoryEntry[]>(can("user.directory") ? "/users/directory" : null);
  const reload = documents.reload;
  const busy = (documents.data ?? []).some((d) => d.status === "pending" || d.status === "processing");

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [busy, reload]);

  const rows = documents.data ?? [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Documents</h1>
          <p className="text-sm text-muted-foreground">Documents you can read in {me.tenant.name}.</p>
        </div>
        {can("document.upload") ? (
          <UploadDialog role={me.user.role} people={people.data ?? []} onUploaded={() => reload()} />
        ) : null}
      </div>
      <ErrorText>{documents.error ? describeError(documents.error) : null}</ErrorText>
      {rows.length === 0 && !documents.loading ? (
        <p className="text-sm text-muted-foreground">
          {can("document.upload")
            ? "Nothing here yet. Upload a document to make it searchable."
            : "No documents have been shared with you yet."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">Document</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Classification</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((document) => (
                <tr key={document.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-3">
                    <div className="flex gap-3">
                      <ClassificationBand level={document.classification} />
                      <div className="min-w-0">
                        <Link href={`/documents/${document.id}`} className="font-medium hover:underline">
                          {document.title}
                        </Link>
                        <p className="truncate text-xs text-muted-foreground">{document.original_filename}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <ClassificationBadge level={document.classification} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusText document={document} />
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                    {formatDate(document.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

```tsx file=frontend/src/app/(app)/documents/[id]/page.tsx
"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import { PermissionEditor } from "@/components/documents/permission-editor";
import { StatusText } from "@/components/documents/status";
import { ErrorText } from "@/components/form";
import { useSession } from "@/components/session";
import { formatBytes, formatDateTime } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { me, can } = useSession();
  const document = useApi<DocumentInfo>(`/documents/${id}`);
  const manage = document.data?.can_manage === true;
  const people = useApi<DirectoryEntry[]>(manage && can("user.directory") ? "/users/directory" : null);

  if (document.error) {
    return (
      <div className="space-y-3">
        <ErrorText>{describeError(document.error)}</ErrorText>
        <Link href="/documents" className="text-sm text-primary hover:underline">
          Back to documents
        </Link>
      </div>
    );
  }
  if (!document.data) return <p className="text-sm text-muted-foreground">Loading document</p>;
  const doc = document.data;

  return (
    <div className="grid max-w-3xl gap-6">
      <Link href="/documents" className="text-sm text-primary hover:underline">
        Back to documents
      </Link>
      <div className="flex gap-4">
        <ClassificationBand level={doc.classification} className="w-1.5" />
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">{doc.title}</h1>
          <ClassificationBadge level={doc.classification} />
        </div>
      </div>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">File</dt>
        <dd className="truncate">{doc.original_filename}</dd>
        <dt className="text-muted-foreground">Size</dt>
        <dd>{formatBytes(doc.size_bytes)}</dd>
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <StatusText document={doc} />
        </dd>
        <dt className="text-muted-foreground">Added</dt>
        <dd>{formatDateTime(doc.created_at)}</dd>
        <dt className="text-muted-foreground">Last changed</dt>
        <dd>{formatDateTime(doc.updated_at)}</dd>
      </dl>
      {manage ? (
        <PermissionEditor
          key={doc.updated_at}
          document={doc}
          role={me.user.role}
          people={people.data ?? []}
          onSaved={() => document.reload()}
          onDeleted={() => router.push("/documents")}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Only the person who uploaded this document and admins can change who can read it.
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Frontend checks** — PASS.
- [ ] **Step 5: README, devlog, commit, push** — `feat(frontend): documents list, upload, and permission editor`

---

### Task 6: People and audit log (admins)

**Files:**
- Create: `frontend/src/lib/audit.ts`, `frontend/src/lib/audit.test.ts`, `frontend/src/app/(app)/admin/users/page.tsx`, `frontend/src/app/(app)/admin/audit/page.tsx`

**Interfaces:**
- Produces: `EVENT_LABELS`, `AuditFilters`, `buildAuditQuery(filters, cursor?) -> string`.

- [ ] **Step 1: Failing test**

```ts file=frontend/src/lib/audit.test.ts
import { describe, expect, it } from "vitest";

import { buildAuditQuery, EVENT_LABELS } from "./audit";

describe("buildAuditQuery", () => {
  it("includes only the filters that are set", () => {
    expect(buildAuditQuery({ eventType: "", userId: "", from: "", to: "" })).toBe("limit=50");
  });

  it("turns dates into a whole-day range in UTC", () => {
    const query = new URLSearchParams(
      buildAuditQuery({ eventType: "rag.query", userId: "u1", from: "2026-10-01", to: "2026-10-02" }, "cur"),
    );
    expect(query.get("event_type")).toBe("rag.query");
    expect(query.get("user_id")).toBe("u1");
    expect(query.get("from")).toBe("2026-10-01T00:00:00Z");
    expect(query.get("to")).toBe("2026-10-02T23:59:59Z");
    expect(query.get("cursor")).toBe("cur");
  });

  it("names every event type in plain words", () => {
    expect(EVENT_LABELS["rag.query"]).toBe("Question asked");
    expect(Object.keys(EVENT_LABELS)).toContain("audit.viewed");
  });
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

```ts file=frontend/src/lib/audit.ts
export const EVENT_LABELS: Record<string, string> = {
  "rag.query": "Question asked",
  "rag.denied": "Question refused",
  "authz.mismatch": "Authorization check failed",
  "auth.login_succeeded": "Signed in",
  "auth.login_failed": "Sign-in failed",
  "document.uploaded": "Document uploaded",
  "document.acl_changed": "Permissions changed",
  "document.deleted": "Document deleted",
  "document.ingested": "Document processed",
  "document.ingest_failed": "Processing failed",
  "user.created": "Person added",
  "user.role_changed": "Role changed",
  "user.activated": "Person reactivated",
  "user.deactivated": "Person deactivated",
  "tenant.updated": "Workspace settings changed",
  "audit.viewed": "Audit log viewed",
};

export interface AuditFilters {
  eventType: string;
  userId: string;
  from: string;
  to: string;
}

export const NO_FILTERS: AuditFilters = { eventType: "", userId: "", from: "", to: "" };

export function buildAuditQuery(filters: AuditFilters, cursor?: string | null): string {
  const params = new URLSearchParams();
  if (filters.eventType) params.set("event_type", filters.eventType);
  if (filters.userId) params.set("user_id", filters.userId);
  if (filters.from) params.set("from", `${filters.from}T00:00:00Z`);
  if (filters.to) params.set("to", `${filters.to}T23:59:59Z`);
  if (cursor) params.set("cursor", cursor);
  params.set("limit", "50");
  return params.toString();
}
```

```tsx file=frontend/src/app/(app)/admin/users/page.tsx
"use client";

import { useState, type FormEvent } from "react";

import { ErrorText, Field, Input, Select } from "@/components/form";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import { ROLE_LABEL, ROLES, type Role, type UserInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function PeoplePage() {
  const { me } = useSession();
  const users = useApi<UserInfo[]>("/users");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPending(true);
    setError(null);
    try {
      await api.post("/users", {
        email: form.get("email"),
        password: form.get("password"),
        role: form.get("role"),
      });
      formElement.reset();
      users.reload();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  async function update(user: UserInfo, change: { role?: Role; is_active?: boolean }) {
    setError(null);
    try {
      await api.patch(`/users/${user.id}`, change);
      users.reload();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <div className="grid max-w-4xl gap-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">People</h1>
        <p className="text-sm text-muted-foreground">
          Roles set how far each person&apos;s clearance reaches. Changes apply to their next request.
        </p>
      </div>
      <form onSubmit={create} className="grid gap-4 rounded-lg border border-border bg-card p-5 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end">
        <Field label="Email" htmlFor="new-email">
          <Input id="new-email" name="email" type="email" required />
        </Field>
        <Field label="Temporary password" htmlFor="new-password" hint="At least 12 characters.">
          <Input id="new-password" name="password" type="password" minLength={12} required autoComplete="new-password" />
        </Field>
        <Field label="Role" htmlFor="new-role">
          <Select id="new-role" name="role" defaultValue="member">
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABEL[role]}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" disabled={pending}>
          Add person
        </Button>
      </form>
      <ErrorText>{error ?? (users.error ? describeError(users.error) : null)}</ErrorText>
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">Person</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Role</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Access</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Added</th>
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).map((user) => {
              const self = user.id === me.user.id;
              return (
                <tr key={user.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-3">
                    {user.email}
                    {self ? <span className="text-muted-foreground"> (you)</span> : null}
                  </td>
                  <td className="px-4 py-3">
                    <label htmlFor={`role-${user.id}`} className="sr-only">
                      Role for {user.email}
                    </label>
                    <Select
                      id={`role-${user.id}`}
                      value={user.role}
                      disabled={self}
                      className="w-36"
                      onChange={(event) => void update(user, { role: event.target.value as Role })}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {ROLE_LABEL[role]}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-4 py-3">
                    {self ? (
                      <span className="text-muted-foreground">Active</span>
                    ) : (
                      <Button
                        variant={user.is_active ? "outline" : "secondary"}
                        size="sm"
                        onClick={() => void update(user, { is_active: !user.is_active })}
                      >
                        {user.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">{formatDate(user.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

```tsx file=frontend/src/app/(app)/admin/audit/page.tsx
"use client";

import { cn } from "cn";
import { useCallback, useEffect, useState } from "react";

import { ErrorText, Field, Input, Select } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { buildAuditQuery, EVENT_LABELS, NO_FILTERS, type AuditFilters } from "@/lib/audit";
import { formatDateTime } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { AuditEvent, AuditPage, UserInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const DECISION_LABEL = { allow: "Allowed", deny: "Denied", error: "Failed" } as const;

function summary(event: AuditEvent): string {
  if (event.event_type === "rag.query") {
    return `${event.result_count ?? 0} passages from ${event.retrieved_document_ids.length} documents`;
  }
  const reason = event.details["reason"] ?? event.details["error"];
  return typeof reason === "string" ? reason.replaceAll("_", " ") : "";
}

export default function AuditPageView() {
  const users = useApi<UserInfo[]>("/users");
  const [filters, setFilters] = useState<AuditFilters>(NO_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(NO_FILTERS);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const emails = new Map((users.data ?? []).map((u) => [u.id, u.email]));

  const load = useCallback(async (query: AuditFilters, after: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const page = await api.get<AuditPage>(`/audit-events?${buildAuditQuery(query, after)}`);
      setEvents((previous) => (after ? [...previous, ...page.events] : page.events));
      setCursor(page.next_cursor);
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(applied, null);
  }, [applied, load]);

  return (
    <div className="grid gap-6">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Every question, sign-in, and permission change in this workspace. Entries cannot be edited
          or deleted. Questions are recorded as a reference code, not their text, unless the workspace
          opts in.
        </p>
      </div>
      <form
        className="grid gap-4 rounded-lg border border-border bg-card p-5 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_10rem_10rem_auto] lg:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filters);
        }}
      >
        <Field label="Event" htmlFor="event-type">
          <Select id="event-type" value={filters.eventType} onChange={(e) => setFilters({ ...filters, eventType: e.target.value })}>
            <option value="">All events</option>
            {Object.entries(EVENT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Person" htmlFor="person">
          <Select id="person" value={filters.userId} onChange={(e) => setFilters({ ...filters, userId: e.target.value })}>
            <option value="">Everyone</option>
            {(users.data ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.email}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="From" htmlFor="from">
          <Input id="from" type="date" value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        </Field>
        <Field label="To" htmlFor="to">
          <Input id="to" type="date" value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        </Field>
        <Button type="submit">Show events</Button>
      </form>
      <ErrorText>{error}</ErrorText>
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">When</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Event</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Outcome</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Who</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Detail</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Trace</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border last:border-b-0">
                <td className="px-4 py-2.5 whitespace-nowrap text-muted-foreground">{formatDateTime(event.occurred_at)}</td>
                <td className="px-4 py-2.5">{EVENT_LABELS[event.event_type] ?? event.event_type}</td>
                <td className={cn("px-4 py-2.5", event.decision !== "allow" && "text-destructive")}>
                  {DECISION_LABEL[event.decision]}
                </td>
                <td className="px-4 py-2.5">
                  {event.actor_user_id ? (emails.get(event.actor_user_id) ?? "Former member") : "System"}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground">{summary(event)}</td>
                <td className="px-4 py-2.5 text-muted-foreground" title={event.trace_id ?? undefined}>
                  {event.trace_id ? event.trace_id.slice(0, 8) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {events.length === 0 && !loading ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">No events match these filters.</p>
        ) : null}
      </div>
      {cursor ? (
        <div>
          <Button variant="outline" disabled={loading} onClick={() => void load(applied, cursor)}>
            Show older events
          </Button>
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Frontend checks** — PASS.
- [ ] **Step 5: README, devlog, commit, push** — `feat(frontend): people management and audit log viewer`

---

### Task 7: End-to-end check, screenshots, docs, review

**Files:**
- Create: `frontend/scripts/screenshots.mjs`, `docs/screenshots/*.png`
- Modify: `frontend/package.json` (dev dep `playwright`, script `screenshots`), `.github/workflows/ci.yml` (pages served through the edge), `README.md`, `docs/devlog.md`, `docs/decisions.md`, spec §13

- [ ] **Step 1: CI smoke** — after "Log in through the web proxy" in `compose-smoke`, add:

```yaml
      - name: Dashboard pages render through the edge
        run: |
          curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/login
          test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/chat)" = "307"
          test "$(curl -s -o /dev/null -w '%{http_code}' -b jar.txt http://127.0.0.1:3000/chat)" = "200"
```

- [ ] **Step 2: Screenshot script** — `pnpm add -D playwright`, `pnpm exec playwright install chromium`; script signs in to a running stack (base URL, workspace, email, password from env), seeds nothing itself, and saves PNGs of `/login`, `/chat` (after asking one question), `/documents`, a document page, `/admin/users`, `/admin/audit` at 1440x900 into `docs/screenshots/`.

```js file=frontend/scripts/screenshots.mjs
// Captures dashboard screenshots from a running stack for the README and visual review.
// Usage: VEYL_URL=http://127.0.0.1:3000 VEYL_WORKSPACE=demo VEYL_EMAIL=... VEYL_PASSWORD=... \
//        VEYL_QUESTION="What flights may employees book?" node scripts/screenshots.mjs
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const base = process.env.VEYL_URL ?? "http://127.0.0.1:3000";
const out = new URL("../../docs/screenshots/", import.meta.url);
await mkdir(out, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const shot = (name) => page.screenshot({ path: new URL(`${name}.png`, out).pathname.replace(/^\/([A-Za-z]:)/, "$1") });

await page.goto(`${base}/login`);
await shot("login");
await page.getByLabel("Workspace").fill(process.env.VEYL_WORKSPACE);
await page.getByLabel("Email").fill(process.env.VEYL_EMAIL);
await page.getByLabel("Password").fill(process.env.VEYL_PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL("**/chat");

await page.getByLabel("Your question").fill(process.env.VEYL_QUESTION ?? "What flights may employees book?");
await page.getByRole("button", { name: "Ask" }).click();
await page.getByRole("list", { name: "Sources" }).first().waitFor({ timeout: 60_000 }).catch(() => {});
await shot("ask");

await page.goto(`${base}/documents`);
await page.getByRole("table").waitFor();
await shot("documents");
await page.getByRole("table").getByRole("link").first().click();
await page.getByText("Last changed").waitFor();
await shot("document");

await page.goto(`${base}/admin/users`);
await page.getByRole("table").waitFor();
await shot("people");
await page.goto(`${base}/admin/audit`);
await page.getByRole("table").waitFor();
await shot("audit");

await browser.close();
console.log("Saved screenshots to docs/screenshots/");
```

- [ ] **Step 3: Local end-to-end with the real model** — rebuild the stack (no stub), provision a workspace, upload two or three documents through the UI or API with different classifications and a member and manager account, run the screenshot script, and **review every screenshot** (read the PNGs); fix layout problems found. Commit the screenshots and reference them in the README.

- [ ] **Step 4: Docs** — decisions D-043 (people directory for managers), D-044 (classification on citations), D-045 (client-side data fetching through the same-origin proxy; native form controls; `proxy.ts` only checks cookie presence), D-046 (visual identity: classification bands). README: Phase 5 Done, Phase 6 Next, "Using the dashboard" with screenshots. Spec §13 Phase 5 done. Devlog summary.

- [ ] **Step 5: Commit, push, CI, final whole-phase review; fix Critical/Important; minors to devlog.**
