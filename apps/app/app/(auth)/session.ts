/**
 * AP-09 09b: the App's side of a signed-in session, server-only. The auth facade (`/auth/v1/*`)
 * does every identity-provider call; this module only carries its answers: it stores the `Session`
 * the facade returns as the App's httpOnly cookie, ends it (facade sign-out, then the cookie), and
 * keeps the PKCE verifier between an email-link request and its callback. No token is ever logged
 * or put in a URL.
 */
import { createHash, randomBytes } from "node:crypto";
import { cookies, headers } from "next/headers";
import { cookieOptions, encodeSession, SESSION_COOKIE, VERIFIER_COOKIE, VERIFIER_MAX_AGE_S, type FacadeSession } from "@/lib/api/cookie";
import { consumerApi, type ConsumerApi } from "@/lib/api/index";
import { accessToken, apiOrigin } from "@/lib/request-api";

if (typeof window !== "undefined") throw new Error("app/(auth)/session.ts is server-only");

const SECURE = process.env.NODE_ENV === "production";

/** The facade as `token` (or anonymous), optionally carrying the PKCE verifier header the callback needs. */
export function facadeApi(token: string | null = null, verifier: string | null = null): ConsumerApi {
  const send: typeof fetch = verifier === null
    ? fetch
    : (input, init) => fetch(input, { ...init, headers: { ...(init?.headers as Record<string, string>), "x-auth-code-verifier": verifier } });
  return consumerApi({ baseUrl: apiOrigin(), fetch: send, session: () => ({ token }) });
}

/** The client as a session the facade just issued (its cookie is not readable until the next request). */
export const sessionApi = (session: FacadeSession) => facadeApi(session.access_token);

export const sessionCookie = (session: FacadeSession) =>
  [SESSION_COOKIE, encodeSession(session, Math.floor(Date.now() / 1000)), cookieOptions(SECURE)] as const;

export async function storeSession(session: FacadeSession): Promise<void> {
  (await cookies()).set(...sessionCookie(session));
}

/** Ends the session at the IdP (best effort: a session already gone is the same success) and drops the cookie. */
export async function endSession(): Promise<void> {
  const token = await accessToken();
  if (token !== null) await facadeApi(token).call("post", "/auth/v1/sign-out").catch(() => null);
  (await cookies()).delete(SESSION_COOKIE);
}

/** A fresh PKCE pair: the verifier stays in an httpOnly cookie, the S256 challenge goes to the facade. */
export async function newChallenge(): Promise<string> {
  const verifier = randomBytes(32).toString("base64url");
  (await cookies()).set(VERIFIER_COOKIE, verifier, cookieOptions(SECURE, VERIFIER_MAX_AGE_S));
  return createHash("sha256").update(verifier).digest("base64url");
}

/** The App's own origin, for the email links (the facade allows only configured web origins). */
export async function origin(): Promise<string> {
  const h = await headers();
  return h.get("origin") ?? `https://${h.get("x-forwarded-host") ?? h.get("host")}`;
}
