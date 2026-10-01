// L1: the Lab's explicit origin and its own session boundary. Cookies are host-only (no domain) and
// named apart from the App's, so a Lab session never rides on, or widens to, the App's.

export const AUTH_COOKIE = "sb-infrx-lab-auth";
export const WORKSPACE_COOKIE = "infrx-lab-workspace";
const DEV_ORIGIN = "http://localhost:3100";

export type LabConfig = { origin: string; supabaseUrl: string; anonKey: string; secure: boolean };

/** null = misconfigured: the guard then reports "unavailable" (fails closed) instead of guessing. */
export function labConfig(env: Record<string, string | undefined>): LabConfig | null {
  const production = env.NODE_ENV === "production";
  const raw = env.NEXT_PUBLIC_LAB_URL || DEV_ORIGIN; // http, so production must name its own
  const supabaseUrl = env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !anonKey) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.origin !== raw.replace(/\/$/, "")) return null; // an origin, no path
  if (production && url.protocol !== "https:") return null;
  return { origin: url.origin, supabaseUrl, anonKey, secure: url.protocol === "https:" };
}

export function authCookieOptions(config: LabConfig) {
  return { name: AUTH_COOKIE, path: "/", sameSite: "lax" as const, secure: config.secure };
}

export function workspaceCookieOptions(config: LabConfig) {
  return { httpOnly: true, path: "/", sameSite: "lax" as const, secure: config.secure, maxAge: 60 * 60 * 24 * 30 };
}

/** LAB-03: each family's old server-only base-URL name, read only when LAB_API_URL is unset. Every family
 *  is served by the one control service (lab-control), so the deploy env collapses to LAB_API_URL; these
 *  keep a six-name deployment working until it does (lab-release-tool), then they can go. */
export const LEGACY_API_URL = {
  control: "LAB_CONTROL_URL", traces: "LAB_TRACES_API_URL", evaluation: "LAB_EVALS_API_URL",
  pipelines: "LAB_PIPELINES_API_URL", releases: "LAB_RELEASES_API_URL", datasets: "LAB_DATASETS_API_URL",
} as const;

/** The lab-api base URL for a family (server-only; never NEXT_PUBLIC_); null = unset, the family is unavailable. */
export function labApiUrl(env: Record<string, string | undefined>, family: keyof typeof LEGACY_API_URL): string | null {
  return env.LAB_API_URL || env[LEGACY_API_URL[family]] || null;
}
