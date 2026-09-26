/**
 * I2A: the App's deployment configuration as code — which variables each environment needs, the
 * auth origins and callbacks per environment, the hosted auth settings the code assumes (P-05) and
 * the release identity. `instrumentation.ts` runs `assertDeployEnv` once when a server starts, so a
 * misconfigured deployment refuses to serve instead of failing on a user's request.
 *
 * Server-only by content: it names the server-only variables, so no client module may import it
 * (tests/i2a I2A-BUNDLE-01). Refusals name variables, never values. Imported by `node --test`, so
 * relative `.ts` imports only (R48).
 */
import { apiBaseUrl } from "../../app/(console)/models/catalog.ts";

export const ENVIRONMENTS = ["production", "preview", "development"] as const;
export type Environment = (typeof ENVIRONMENTS)[number];
type Env = Record<string, string | undefined>;

/** The production App origin and the production auth/DB project (public identifiers, not secrets). */
export const PRODUCTION_ORIGIN = "https://app.callbill.ai";
export const PRODUCTION_SUPABASE_URL = "https://fcbnscgsymzdykendbrc.supabase.co";
/**
 * The Vercel scope slug preview hosts end in (`infrx-app-<hash|git-branch>-<scope>.vercel.app`).
 * ⚠️ Operator confirms: apps/app/supabase/README.md records `humanbit`; HANDOFF.md places the
 * project in a personal scope. A wrong slug only refuses previews that set NEXT_PUBLIC_APP_URL.
 */
export const PREVIEW_SCOPE = "humanbit";
const PREVIEW_HOST = new RegExp(`^infrx-app-[a-z0-9-]+-${PREVIEW_SCOPE}\\.vercel\\.app$`);
const DEV_ORIGIN = "http://localhost:3000";
export const CALLBACK_PATH = "/auth/callback";

/** Is `origin` (scheme://host[:port], no path) an App origin for `environment`? */
export function originAllowed(environment: Environment, origin: string): boolean {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (url.origin !== origin.replace(/\/$/, "")) return false;
  if (environment === "production") return url.origin === PRODUCTION_ORIGIN;
  if (environment === "preview") return url.protocol === "https:" && url.port === "" && PREVIEW_HOST.test(url.hostname);
  return url.origin === DEV_ORIGIN;
}

/**
 * Supabase Authentication → URL Configuration → Redirect URLs, per auth project. The production
 * project accepts only the production callback; previews and local development use a separate
 * (staging) project, because a preview may never hold production write credentials.
 */
export const REDIRECT_ALLOWLIST = Object.freeze({
  production: [`${PRODUCTION_ORIGIN}${CALLBACK_PATH}**`],
  staging: [`https://infrx-app-*-${PREVIEW_SCOPE}.vercel.app${CALLBACK_PATH}**`, `${DEV_ORIGIN}${CALLBACK_PATH}**`],
});

/** The hosted auth settings (P-05) the App's flows assume. Set by the operator; never by code. */
export const P05_SETTINGS: readonly { setting: string; expected: string }[] = Object.freeze([
  { setting: "Site URL", expected: `${PRODUCTION_ORIGIN} (production project)` },
  { setting: "Redirect URLs", expected: `production project: ${REDIRECT_ALLOWLIST.production.join(", ")} only; staging project: ${REDIRECT_ALLOWLIST.staging.join(", ")}` },
  { setting: "Confirm email", expected: "on (unverified sign-in off)" },
  { setting: "Allow new users to sign up", expected: "on (public verified signup)" },
  { setting: "Minimum password length", expected: "8 or more (the client hint is 8)" },
  { setting: "Custom SMTP", expected: "configured (the default sender is restricted and rate-limited)" },
  { setting: "Rate limits", expected: "emails/hour, sign-ups, sign-ins and verifications per IP set for launch" },
  { setting: "Email templates", expected: "confirm signup and reset password link to {{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email|recovery&next=/welcome|/update-password (cross-device), or the default {{ .ConfirmationURL }}" },
  { setting: "infrx.feature_flags.signup_grant", expected: "enabled (audited operator action) when the grant opens" },
]);

type Check = (value: string, environment: Environment) => string | null;

const httpsOrigin: Check = (value, environment) => {
  try {
    const url = new URL(value);
    const loopback = environment === "development" && url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname);
    if (url.protocol !== "https:" && !loopback) return "must be https";
    if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) return "must be an origin with no path";
    return null;
  } catch {
    return "is not a URL";
  }
};

export type Variable = {
  name: string;
  /** `public` values are inlined into browser bundles by Next; `server` values never may be. */
  exposure: "public" | "server";
  required: readonly Environment[];
  check?: Check;
};

/** Every variable the App reads, per environment. The runbook's matrix is this list. */
export const VARIABLES: readonly Variable[] = Object.freeze([
  { name: "NEXT_PUBLIC_SUPABASE_URL", exposure: "public", required: ENVIRONMENTS, check: httpsOrigin },
  { name: "NEXT_PUBLIC_SUPABASE_ANON_KEY", exposure: "public", required: ENVIRONMENTS },
  {
    name: "NEXT_PUBLIC_APP_URL",
    exposure: "public",
    // Nothing reads it (auth callbacks use window.location.origin), so its absence must not stop
    // production (fix round 1-I2A-R2; I2A-ENV-09); a value that is set must still be this environment's.
    required: [],
    check: (value, environment) => (originAllowed(environment, value) ? null : `is not an allowed ${environment} origin`),
  },
  { name: "SUPABASE_SERVICE_ROLE_KEY", exposure: "server", required: ["production"] },
  {
    name: "INFRX_API_BASE_URL",
    exposure: "server",
    required: ["production"],
    check: (value, environment) => httpsOrigin(value, environment) ?? (apiBaseUrl({ INFRX_API_BASE_URL: value }) ? null : "is not a valid API origin"),
  },
  {
    name: "CONSOLE_CURSOR_SECRET",
    exposure: "server",
    required: ["production"],
    check: (value) => (value.length >= 16 ? null : "must be at least 16 characters"),
  },
]);

/** The stated environment. A production build (`NODE_ENV=production`) must state it; nothing is guessed. */
export function environmentOf(env: Env): Environment {
  const vercel = env.VERCEL_ENV?.trim() || undefined;
  const own = env.INFRX_APP_ENVIRONMENT?.trim() || undefined;
  if (vercel && own && vercel !== own) throw new Error("VERCEL_ENV and INFRX_APP_ENVIRONMENT disagree");
  const stated = vercel ?? own;
  if (stated === undefined) {
    if (env.NODE_ENV === "production") throw new Error("a production build must state VERCEL_ENV or INFRX_APP_ENVIRONMENT");
    return "development";
  }
  if (!(ENVIRONMENTS as readonly string[]).includes(stated)) {
    throw new Error("VERCEL_ENV/INFRX_APP_ENVIRONMENT must be production, preview or development");
  }
  return stated as Environment;
}

const sameOrigin = (value: string | undefined, origin: string) => {
  try {
    return value !== undefined && new URL(value).origin === origin;
  } catch {
    return false;
  }
};

/** The environment, or an Error naming every problem (variable names only). Fails closed. */
export function assertDeployEnv(env: Env): Environment {
  const environment = environmentOf(env);
  const problems: string[] = [];
  for (const variable of VARIABLES) {
    const value = env[variable.name]?.trim();
    if (!value) {
      if (variable.required.includes(environment)) problems.push(`${variable.name} is required in ${environment}`);
      continue;
    }
    const problem = variable.check?.(value, environment);
    if (problem) problems.push(`${variable.name} ${problem}`);
  }
  const productionProject = sameOrigin(env.NEXT_PUBLIC_SUPABASE_URL?.trim(), PRODUCTION_SUPABASE_URL);
  if (environment === "production" && env.NEXT_PUBLIC_SUPABASE_URL?.trim() && !productionProject) {
    problems.push("NEXT_PUBLIC_SUPABASE_URL is not the production project");
  }
  if (environment === "preview" && productionProject) {
    // The marker is the project, not a key: a preview on the production project can sign users up,
    // send production mail and, with the service-role key, write money.
    problems.push("NEXT_PUBLIC_SUPABASE_URL is the production project; a preview must use a non-production project");
  }
  if (problems.length > 0) throw new Error(`App environment refused (${environment}): ${problems.join("; ")}`);
  return environment;
}

export type ReleaseIdentity = {
  commit: string;
  builtAt: string;
  deployment: string;
  environment: string;
  apiOrigin: string;
};

const pick = (value: string | undefined, shape: RegExp) => (value && shape.test(value) ? value : undefined);
const SHA = /^[0-9a-f]{40}$/;

/**
 * The host's commit (`VERCEL_GIT_COMMIT_SHA`) wins when present; `INFRX_RELEASE_SHA` is the
 * off-Vercel build input only. Both present and different → "unknown": a stale operator value
 * never relabels a build (fix round 1-I2A-R3). "" (next.config.ts's bake of an absent one) is absent.
 */
function releaseCommit(env: Env): string {
  const host = env.VERCEL_GIT_COMMIT_SHA || undefined;
  const own = env.INFRX_RELEASE_SHA || undefined;
  if (host) return own && own !== host ? "unknown" : (pick(host, SHA) ?? "unknown");
  return pick(own, SHA) ?? "unknown";
}

/** What is running: from the build and the host, with "unknown" for anything absent or malformed. */
export function releaseIdentity(env: Env): ReleaseIdentity {
  let environment = "unknown";
  try {
    environment = environmentOf(env);
  } catch {
    // stays "unknown"
  }
  return {
    commit: releaseCommit(env),
    builtAt: pick(env.INFRX_BUILT_AT, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/) ?? "unknown",
    deployment: pick(env.VERCEL_DEPLOYMENT_ID, /^dpl_[A-Za-z0-9]+$/) ?? "unknown",
    environment,
    apiOrigin: apiBaseUrl(env) ?? "unknown",
  };
}
