import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";
import { api } from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

beforeEach(() => window.history.pushState({}, "", "/documents"));

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
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?next=%2Fdocuments"));
    expect(screen.queryByText(/mia/)).not.toBeInTheDocument();
  });
  it("sends the user to sign in when the session ends mid-use", async () => {
    const me = {
      user: { id: "u1", email: "mia@acme.test", role: "member" },
      tenant: { id: "t1", slug: "acme", name: "Acme" },
      permissions: ["rag.query"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          url.endsWith("/auth/me")
            ? Response.json(me)
            : Response.json({ error: { code: "unauthenticated", message: "x", trace_id: "t" } }, { status: 401 }),
        ),
      ),
    );
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await screen.findByText("mia@acme.test reader");
    await api.get("/documents").catch(() => undefined);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?next=%2Fdocuments"));
  });
});
