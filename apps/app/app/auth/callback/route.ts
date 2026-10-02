import { NextResponse, type NextRequest } from "next/server";
import { VERIFIER_COOKIE, type FacadeSession } from "@/lib/api/cookie";
import { claimGrant, completeCallback } from "@/app/(auth)/flow";
import { facadeApi, sessionApi, sessionCookie } from "@/app/(auth)/session";

/**
 * The browser arrives here from an email link, with ?code=… (PKCE) or ?token_hash=…&type=… .
 * `completeCallback` (app/(auth)/flow.ts, tests/a) has the facade verify it (with the verifier this
 * App kept), keeps the session, claims the one-time grant as that session, and picks a same-site
 * path; failures become fixed codes. The verifier is single-use: dropped whatever happened.
 */
export async function GET(request: NextRequest) {
  const verifier = request.cookies.get(VERIFIER_COOKIE)?.value ?? null;
  let issued: FacadeSession | null = null;
  const target = await completeCallback(request.nextUrl.searchParams, {
    land: (params) => facadeApi(null, verifier).call("get", "/auth/v1/callback", { query: Object.fromEntries(params) }),
    store: async (session) => {
      issued = session;
    },
    claim: (session) => claimGrant(sessionApi(session)),
  });
  const response = NextResponse.redirect(new URL(target, request.nextUrl.origin));
  if (issued !== null) response.cookies.set(...sessionCookie(issued));
  response.cookies.delete(VERIFIER_COOKIE);
  return response;
}
