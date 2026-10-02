import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/api/cookie";

/**
 * AP-09 09e: a session infrx-api no longer accepts. The cookie is dropped here (a page cannot set
 * cookies) before the login page, which would otherwise bounce a stale cookie back to the console.
 */
export function GET(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/login?error=session_ended", request.nextUrl.origin));
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
