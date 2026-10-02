// L1: the Lab's explicit origin and its own session boundary. Cookies are host-only (no domain),
// http-only and named apart from the App's, so a Lab session never rides on, or widens to, the App's.
// AP-09 09c: the one API origin is LAB_API_URL (server-only); the Lab holds no Supabase URL or key.

/** The signed-in user's access token, as the auth facade issued it (the Bearer every API call forwards). */
export const AUTH_COOKIE = "infrx-lab-session";
/** Its refresh token: read only by the proxy's refresh (routes.ts). */
export const REFRESH_COOKIE = "infrx-lab-refresh";
export const WORKSPACE_COOKIE = "infrx-lab-workspace";
const DEV_ORIGIN = "http://localhost:3100";

export type LabConfig = { origin: string; apiUrl: string; secure: boolean };

/** The lab-api base URL every family uses (server-only; never NEXT_PUBLIC_); null = unset. Register
 *  row 76: the six per-family names it replaced are read nowhere. */
export function labApiUrl(env: Record<string, string | undefined>): string | null {
  return env.LAB_API_URL || null;
}

/** null = misconfigured: the guard then reports "unavailable" (fails closed) instead of guessing. */
export function labConfig(env: Record<string, string | undefined>): LabConfig | null {
  const production = env.NODE_ENV === "production";
  const raw = env.NEXT_PUBLIC_LAB_URL || DEV_ORIGIN; // http, so production must name its own
  const apiUrl = labApiUrl(env);
  if (!apiUrl) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.origin !== raw.replace(/\/$/, "")) return null; // an origin, no path
  if (production && url.protocol !== "https:") return null;
  return { origin: url.origin, apiUrl, secure: url.protocol === "https:" };
}

/** The session cookies' options: page script never reads a token. */
export function authCookieOptions(config: LabConfig) {
  return { httpOnly: true, path: "/", sameSite: "lax" as const, secure: config.secure, maxAge: 60 * 60 * 24 * 30 };
}

/** The workspace preference cookie: the same boundary as the session's. */
export const workspaceCookieOptions = authCookieOptions;
