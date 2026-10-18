import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, onSessionLost } from "./api";

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

  it("reports a lost session on any 401 except the sign-in request itself", async () => {
    const handler = vi.fn();
    const stop = onSessionLost(handler);
    const expired = () =>
      new Response(JSON.stringify({ error: { code: "unauthenticated", message: "x", trace_id: "t" } }), {
        status: 401,
      });
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(expired())));
    await api.get("/documents").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await api.post("/auth/login", {}).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    stop();
    await api.get("/documents").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
