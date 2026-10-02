// `.js`: the package has no exports map, and `node --test` loads this file (tests/a/middleware.test.ts).
import { NextResponse, type NextRequest } from "next/server.js";
import { consumerApi } from "./api/index.ts";
import { cookieOptions, decodeSession, encodeSession, needsRefresh, SESSION_COOKIE, type FacadeSession } from "./api/cookie.ts";
import { apiBaseUrl } from "../app/(console)/models/catalog.ts";

const PUBLIC = ["/api/version", "/api/client-errors", "/login", "/signup", "/verify-email", "/forgot-password", "/auth"];

/** A refresh's outcome: a new session, the session ended (sign in again), or no answer (keep it). */
export type Renewal = FacadeSession | "ended" | "unavailable";

/** `POST /auth/v1/refresh` through the facade. Only a 401 ends the session; an outage never signs anyone out. */
export async function facadeRefresh(refreshToken: string, send: typeof fetch = fetch): Promise<Renewal> {
  const api = consumerApi({ baseUrl: apiBaseUrl(process.env) ?? "", fetch: send });
  try {
    const answer = await api.call("post", "/auth/v1/refresh", { body: { refresh_token: refreshToken } });
    if (answer.ok) return answer.data;
    return answer.error.status === 401 ? "ended" : "unavailable";
  } catch {
    return "unavailable";
  }
}

/**
 * Keeps the App's session cookie fresh (the facade's refresh, shortly before the access token
 * expires) and guards the console routes. Whether a token is still good is infrx-api's call on
 * every read (a refused one goes to /auth/expired); this only decides who is signed in at all.
 */
export async function updateSession(request: NextRequest, refresh: (token: string) => Promise<Renewal> = facadeRefresh) {
  const stored = decodeSession(request.cookies.get(SESSION_COOKIE)?.value);
  let signedIn = stored !== null;
  let set: { value: string } | "clear" | null = null;
  const now = Math.floor(Date.now() / 1000);
  if (stored !== null && needsRefresh(stored, now)) {
    const renewed = await refresh(stored.refresh);
    if (renewed === "ended") {
      signedIn = false;
      set = "clear";
      request.cookies.delete(SESSION_COOKIE);
    } else if (renewed !== "unavailable") {
      set = { value: encodeSession(renewed, now) };
      request.cookies.set(SESSION_COOKIE, set.value);
    }
  }
  const to = redirectFor(request, signedIn);
  const response = to === null ? NextResponse.next({ request }) : NextResponse.redirect(to);
  if (set === "clear") response.cookies.delete(SESSION_COOKIE);
  else if (set !== null) response.cookies.set(SESSION_COOKIE, set.value, cookieOptions(process.env.NODE_ENV === "production"));
  return response;
}

/** The guard's decision: where to send this request, or `null` to serve it. */
export function redirectFor(request: NextRequest, signedIn: boolean) {
  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC.some((p) => path === p || path.startsWith(p + "/"));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", path + request.nextUrl.search);
    return url;
  }
  // Only a navigation is sent away: the login form's server action (claimOnboarding) POSTs to /login
  // right after sign-in set the cookie, and redirecting it dropped the first-login claim (E3A F-1).
  if (signedIn && (path === "/login" || path === "/signup") && (request.method === "GET" || request.method === "HEAD")) {
    const url = request.nextUrl.clone();
    url.pathname = "/models";
    url.search = "";
    return url;
  }
  return null;
}
