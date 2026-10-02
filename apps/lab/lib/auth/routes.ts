// WR-L1-6 + AP-09 09c: the two request-level entry points (wired by app/auth/callback/route.ts and
// proxy.ts), through the auth facade. Both write the session cookies onto the response; neither
// decides access (the guard does, per page and action).
// `.js`: next has no exports map and node --test loads this file (tests/l/shell/session.test.ts).
import { NextResponse, type NextRequest } from "next/server.js";
import { labApi } from "../api/index.ts";
import { AUTH_COOKIE, REFRESH_COOKIE, labConfig } from "./config.ts";
import { clearSession, isTokens, refreshDue, storeSession } from "./session.ts";

/** Email links land here. Only sign-in links verify (signup and recovery are the App's); always home,
 *  whatever the link or the facade names. */
export async function authCallback(request: NextRequest): Promise<NextResponse> {
  const config = labConfig(process.env);
  const response = NextResponse.redirect(new URL("/", config?.origin ?? request.nextUrl.origin));
  if (config === null) return response;
  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type");
  const query = code ? { code } : tokenHash && (type === "magiclink" || type === "email") ? { token_hash: tokenHash, type } : null;
  if (query === null) return response;
  const answer = await labApi({ baseUrl: config.apiUrl }).call("get", "/auth/v1/callback", { query });
  const session = answer.ok ? answer.data?.session : null;
  if (isTokens(session)) storeSession(response.cookies, config, session);
  return response;
}

/** The proxy: refresh the Lab session near its expiry, onto this request and the response; a refused
 *  refresh token ends the session, an unreachable facade leaves it for the next request. Never redirects. */
export async function refreshSession(request: NextRequest): Promise<NextResponse> {
  const config = labConfig(process.env);
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (config === null || !refresh || !refreshDue(request.cookies.get(AUTH_COOKIE)?.value)) return NextResponse.next({ request });
  const answer = await labApi({ baseUrl: config.apiUrl }).call("post", "/auth/v1/refresh", { body: { refresh_token: refresh } });
  if (answer.ok && isTokens(answer.data)) {
    request.cookies.set(AUTH_COOKIE, answer.data.access_token);
    request.cookies.set(REFRESH_COOKIE, answer.data.refresh_token);
    const response = NextResponse.next({ request });
    storeSession(response.cookies, config, answer.data);
    return response;
  }
  if (!answer.ok && answer.error.status === 401) {
    clearSession(request.cookies);
    const response = NextResponse.next({ request });
    clearSession(response.cookies);
    return response;
  }
  return NextResponse.next({ request });
}
