// AP-09 09b: the App's own session cookie, set from the auth facade's `Session` (R271: the IdP
// keeps every session; Next only carries it). Pure, so node --test loads it (R48).
//
// One httpOnly cookie holds the access token, the refresh token and the expiry instant. Nothing
// here verifies a token: infrx-api checks the bearer on every call (SessionActors), so a forged or
// stale cookie reads nothing.
import type { components } from "@infrx/api-client/consumer";

export type FacadeSession = components["schemas"]["Session"];
export type StoredSession = { access: string; refresh: string; expiresAt: number };

export const SESSION_COOKIE = "infrx-session";
/** The PKCE verifier between an email-link request and its callback (the facade forwards the challenge). */
export const VERIFIER_COOKIE = "infrx-auth-verifier";
/** The refresh token outlives the access token; the cookie lasts as long as a refresh could succeed. */
export const SESSION_MAX_AGE_S = 60 * 60 * 24 * 30;
export const VERIFIER_MAX_AGE_S = 60 * 60;
/** Refresh this long before the access token expires, so a page render never races its expiry. */
export const REFRESH_SKEW_S = 60;

export function cookieOptions(secure: boolean, maxAge = SESSION_MAX_AGE_S) {
  return { httpOnly: true, secure, sameSite: "lax" as const, path: "/", maxAge };
}

// base64url without Buffer: the middleware runs on the edge runtime too.
const toB64url = (text: string) => btoa(String.fromCharCode(...new TextEncoder().encode(text))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (value: string) => {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/");
  return new TextDecoder().decode(Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (c) => c.charCodeAt(0)));
};

export function encodeSession(session: FacadeSession, nowS: number): string {
  const expiresAt = session.expires_at ?? nowS + session.expires_in;
  const stored: StoredSession = { access: session.access_token, refresh: session.refresh_token, expiresAt };
  return toB64url(JSON.stringify(stored));
}

/** The stored session, or null for anything that is not exactly one. */
export function decodeSession(value: string | undefined | null): StoredSession | null {
  if (typeof value !== "string" || value === "") return null;
  try {
    const parsed: unknown = JSON.parse(fromB64url(value));
    if (typeof parsed !== "object" || parsed === null) return null;
    const { access, refresh, expiresAt } = parsed as Record<string, unknown>;
    if (typeof access !== "string" || access === "" || typeof refresh !== "string" || refresh === "") return null;
    if (typeof expiresAt !== "number" || !Number.isFinite(expiresAt)) return null;
    return { access, refresh, expiresAt };
  } catch {
    return null;
  }
}

export function needsRefresh(session: StoredSession, nowS: number): boolean {
  return session.expiresAt - REFRESH_SKEW_S <= nowS;
}
