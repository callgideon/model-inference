// AP-09 09c: the Lab session is the auth facade's (/auth/v1/* on LAB_API_URL): its access and refresh
// tokens live in two http-only, host-only cookies (config.ts) that only the Lab's server reads. It
// replaces the Supabase session client (readOnlyClient) and its cookie format. Next's request APIs
// load on use, so the ports that import this still load under node --test.
import { labApi, type ApiError, type LabApi, type Tokens } from "../api/index.ts";
import { AUTH_COOKIE, REFRESH_COOKIE, authCookieOptions, labConfig, type LabConfig } from "./config.ts";

/** A cookie store that can write: Next's `cookies()` in an action, or a response's `cookies`. */
export type Jar = { set(name: string, value: string, options?: object): unknown; delete(name: string): unknown };

/** A facade answer is a session only when both tokens are text: anything else stores nothing. */
export const isTokens = (value: unknown): value is Tokens =>
  typeof (value as Tokens | null)?.access_token === "string" && typeof (value as Tokens).refresh_token === "string";

export function storeSession(jar: Jar, config: LabConfig, tokens: Tokens): void {
  jar.set(AUTH_COOKIE, tokens.access_token, authCookieOptions(config));
  jar.set(REFRESH_COOKIE, tokens.refresh_token, authCookieOptions(config));
}

export function clearSession(jar: Pick<Jar, "delete">): void {
  jar.delete(AUTH_COOKIE);
  jar.delete(REFRESH_COOKIE);
}

/** Refresh within this many seconds of the access token's expiry. */
export const REFRESH_MARGIN_S = 60;

/** Whether the access token is due a refresh: its `exp` is read unverified, only to time the refresh
 *  (the API verifies every token); an unreadable or missing token is due. */
export function refreshDue(access: string | undefined, nowMs = Date.now()): boolean {
  try {
    const payload = access!.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const exp = (JSON.parse(atob(payload)) as { exp?: unknown }).exp;
    return typeof exp !== "number" || exp - nowMs / 1000 <= REFRESH_MARGIN_S;
  } catch {
    return true;
  }
}

/** A refused sign-in (wrong credentials, unconfirmed, malformed) is `failed`; a limit or an outage
 *  is `unavailable` - never the auth server's words. */
export const signInFailure = (error: ApiError): "failed" | "unavailable" =>
  error.kind === "error" && error.status >= 400 && error.status < 500 && error.status !== 429 ? "failed" : "unavailable";

/** The signed-in user's access token from the Lab session cookie; null without a session. */
export async function sessionToken(): Promise<string | null> {
  const { cookies } = await import("next/headers");
  return (await cookies()).get(AUTH_COOKIE)?.value || null;
}

/** The Lab port as the signed-in user (server only); null when the Lab is misconfigured. */
export function sessionApi(env: Record<string, string | undefined> = process.env): LabApi | null {
  const config = labConfig(env);
  return config === null ? null : labApi({ baseUrl: config.apiUrl, session: async () => ({ token: await sessionToken() }) });
}
