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
