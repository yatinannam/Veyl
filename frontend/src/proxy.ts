import { NextResponse, type NextRequest } from "next/server";

export const PROXY_SECRET_HEADER = "x-veyl-proxy-secret";
export const CLIENT_IP_HEADER = "x-veyl-client-ip";

/** The visitor's address as reported by the edge in front of this server. Vercel and the
 * Compose edge (Caddy) both replace any X-Forwarded-For the client sent, so its first entry
 * is the real peer. X-Real-IP is not used: Caddy passes a client's value through. */
export function visitorIp(headers: Headers): string | null {
  return headers.get("x-forwarded-for")?.split(",")[0]?.trim() || null;
}

/** Forward /api/* to the API, read at request time so one build serves any host. With
 * PROXY_SHARED_SECRET set, the API is told the visitor's address and can trust it because
 * the secret never leaves the server; values a client sent in those headers are dropped. */
export function forwardToApi(request: NextRequest): NextResponse {
  const origin = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  const target = new URL(`${request.nextUrl.pathname}${request.nextUrl.search}`, origin);
  const headers = new Headers(request.headers);
  headers.delete(PROXY_SECRET_HEADER);
  headers.delete(CLIENT_IP_HEADER);
  const secret = process.env.PROXY_SHARED_SECRET;
  if (secret) {
    headers.set(PROXY_SECRET_HEADER, secret);
    const ip = visitorIp(request.headers);
    if (ip) headers.set(CLIENT_IP_HEADER, ip);
  }
  return NextResponse.rewrite(target, { request: { headers } });
}

/** For pages, convenience only: requests without a session cookie go to sign-in before
 * rendering. The API decides every permission; a present-but-stale cookie is handled
 * client-side. */
export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/api/")) return forwardToApi(request);
  if (request.cookies.has("veyl_session")) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/api/:path*", "/chat/:path*", "/documents/:path*", "/admin/:path*"],
};
