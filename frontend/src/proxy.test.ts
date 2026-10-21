import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { proxy, visitorIp } from "./proxy";

const SECRET = "s".repeat(40);

/** Request headers the proxy sends upstream, as Next encodes them on the response. */
function forwarded(response: Response, name: string): string | null {
  return response.headers.get(`x-middleware-request-${name}`);
}

function apiRequest(headers: Record<string, string> = {}) {
  return new NextRequest("https://veyl.example/api/v1/auth/login?x=1", {
    method: "POST",
    headers,
  });
}

describe("proxy for /api/*", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("forwards to the API chosen at request time", () => {
    vi.stubEnv("API_INTERNAL_URL", "https://veyl-api.example");
    const response = proxy(apiRequest());
    expect(response.headers.get("x-middleware-rewrite")).toBe(
      "https://veyl-api.example/api/v1/auth/login?x=1",
    );
  });

  it("names the visitor to the API with the shared secret", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", SECRET);
    const response = proxy(apiRequest({ "x-forwarded-for": "203.0.113.5" }));
    expect(forwarded(response, "x-veyl-proxy-secret")).toBe(SECRET);
    expect(forwarded(response, "x-veyl-client-ip")).toBe("203.0.113.5");
  });

  it("drops a client's own secret and visitor headers", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", "");
    const response = proxy(
      apiRequest({ "x-veyl-proxy-secret": "guess", "x-veyl-client-ip": "198.51.100.1" }),
    );
    expect(forwarded(response, "x-veyl-proxy-secret")).toBeNull();
    expect(forwarded(response, "x-veyl-client-ip")).toBeNull();
    expect(response.headers.get("x-middleware-override-headers")).not.toContain("x-veyl");
  });

  it("replaces a forged visitor header when the secret is set", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", SECRET);
    const response = proxy(
      apiRequest({ "x-forwarded-for": "203.0.113.5", "x-veyl-client-ip": "198.51.100.1" }),
    );
    expect(forwarded(response, "x-veyl-client-ip")).toBe("203.0.113.5");
  });
});

describe("proxy for pages", () => {
  it("sends visitors without a session to sign-in", () => {
    const response = proxy(new NextRequest("https://veyl.example/chat"));
    expect(response.headers.get("location")).toBe("https://veyl.example/login?next=%2Fchat");
  });
});

describe("visitorIp", () => {
  it("takes the first X-Forwarded-For entry, which the edge sets", () => {
    const headers = new Headers({ "x-forwarded-for": "203.0.113.5, 10.0.0.1" });
    expect(visitorIp(headers)).toBe("203.0.113.5");
  });

  it("ignores X-Real-IP, which an edge may pass through from the client", () => {
    expect(visitorIp(new Headers({ "x-real-ip": "198.51.100.1" }))).toBeNull();
  });
});
