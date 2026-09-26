/**
 * I3: what the App reports about its own errors — the release, the route with its ids replaced,
 * the error digest and the error's class name — and no free text: never the message (it can hold
 * an email, a key or a body excerpt, and no denylist catches all of them), the stack, the query,
 * a request header or a body. One JSON line per error on the function log (Vercel's logs are the
 * sink; no vendor SDK). The shape is `ErrorLine`; infra/app/operations.md ("Browser error
 * monitoring") documents it and tests/i3 pins the two together.
 *
 * Client-safe: no imports, no server-only name. Imported by the error pages (browser), the
 * /api/client-errors route, instrumentation.ts and `node --test`.
 */

export const REPORT_PATH = "/api/client-errors";
/** A browser report larger than this is refused (413); the route never reads past it. */
export const REPORT_MAX_BYTES = 2048;
export const ROUTE_MAX = 200;

export type Release = { commit: string; deployment: string; environment: string };

export type ErrorLine = {
  event: "app_error";
  source: "server" | "browser";
  commit: string;
  deployment: string;
  environment: string;
  /** A path or route pattern: no query, no fragment, every segment outside the App's own words `[id]`. */
  route: string;
  /** Next's error digest (`<hash>` or `<hash>@E<code>`), or null. */
  digest: string | null;
  /** The error's class name (`TypeError`, `ChunkLoadError`), or null. Never its message. */
  name: string | null;
};

const NAME = /^[A-Za-z]{0,40}(?:Error|Exception)$/;
/** A class name only (letters, ending Error/Exception); a name used to carry text is dropped. */
export const safeName = (value: unknown): string | null =>
  typeof value === "string" && NAME.test(value) ? value : null;

const DIGEST = /^\d{1,20}(?:@E\d{1,6})?$/;
/** Next's digest shape only; anything else (a smuggled value) is dropped, never logged. */
export const safeDigest = (value: unknown): string | null =>
  typeof value === "string" && DIGEST.test(value) ? value : null;

/**
 * The App's route words (lowercase, hyphenated) and Next's patterns (`[requestId]`, `(console)`)
 * are kept; anything else — an id, an email, a name, anything percent-encoded — is not ours to log.
 */
const SEGMENT = /^(?:_?[a-z]+(?:-[a-z]+)*|\[{1,2}(?:\.{3})?[A-Za-z]+\]{1,2}|\([a-z-]+\))?$/;

/** A path without its query and fragment (a callback's `token_hash` lives there), ids as `[id]`. */
export function safeRoute(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/")) return "unknown";
  const segments = value.split(/[?#]/, 1)[0].split("/");
  const path = segments.map((segment) => (SEGMENT.test(segment) ? segment : "[id]")).join("/");
  return path.length > ROUTE_MAX ? `${path.slice(0, ROUTE_MAX)}…` : path;
}

function line(source: ErrorLine["source"], release: Release, route: string, digest: string | null, name: string | null): ErrorLine {
  const { commit, deployment, environment } = release;
  return { event: "app_error", source, commit, deployment, environment, route, digest, name };
}

const field = (error: unknown, key: "digest" | "name"): unknown =>
  typeof error === "object" && error !== null && key in error ? (error as Record<string, unknown>)[key] : undefined;

/** instrumentation.ts `onRequestError`: the route pattern (`/usage/[requestId]`, no ids), digest and class name. */
export function serverErrorLine(error: unknown, routePath: string, release: Release): ErrorLine {
  return line("server", release, safeRoute(routePath), safeDigest(field(error, "digest")), safeName(field(error, "name")));
}

export type Parsed = { status: 204; line: ErrorLine } | { status: 400 | 415 };

/** POST /api/client-errors: a small JSON `{digest, route, name}` → one line, or a refusal status. */
export function parseClientReport(contentType: string | null, body: string, release: Release): Parsed {
  if (!/^application\/json\s*(?:;|$)/i.test(contentType ?? "")) return { status: 415 };
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    return { status: 400 };
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { status: 400 };
  const { digest, route, name } = raw as Record<string, unknown>;
  return { status: 204, line: line("browser", release, safeRoute(route), safeDigest(digest), safeName(name)) };
}

/** What an error page sends: the same sanitised fields, so nothing else leaves the browser. */
export function browserReport(error: { name?: unknown; digest?: unknown }, pathname: string) {
  return { digest: safeDigest(error.digest), route: safeRoute(pathname), name: safeName(error.name) };
}

/**
 * The release commit's short form for the error page, from the build-time inputs. Same rule as
 * lib/deploy/env.ts `releaseIdentity` (server-only, so not importable here; tests/i3 pins them
 * equal): the host's commit wins; both present and different, or malformed, is "unknown".
 */
export function shortCommit(env: { VERCEL_GIT_COMMIT_SHA?: string; INFRX_RELEASE_SHA?: string }): string {
  const host = env.VERCEL_GIT_COMMIT_SHA || undefined;
  const own = env.INFRX_RELEASE_SHA || undefined;
  const commit = host ? (own && own !== host ? undefined : host) : own;
  return commit && /^[0-9a-f]{40}$/.test(commit) ? commit.slice(0, 7) : "unknown";
}

/**
 * At most `limit` admissions per `windowMs`. ponytail: per server instance (a log-flood bound), not
 * per client; per-client limiting is a Vercel firewall rule [OP] (infra/app/operations.md).
 */
export function limiter(limit: number, windowMs: number) {
  let start = -Infinity;
  let used = 0;
  return (now: number): boolean => {
    if (now - start >= windowMs) {
      start = now;
      used = 0;
    }
    used += 1;
    return used <= limit;
  };
}
