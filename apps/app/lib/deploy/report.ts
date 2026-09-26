/**
 * I3: what the App reports about its own errors — the release, the route and the error digest —
 * and nothing that could carry a person's data (no key, token, email, URL, prompt, body, stack or
 * request header). One JSON line per error on the function log (Vercel's logs are the sink; no
 * vendor SDK). The shape is `ErrorLine`; infra/app/operations.md ("Browser error monitoring") documents it and
 * tests/i3 pins the two together.
 *
 * Client-safe: no imports, no server-only name. Imported by the error pages (browser), the
 * /api/client-errors route, instrumentation.ts and `node --test`.
 */

export const REPORT_PATH = "/api/client-errors";
/** A browser report larger than this is refused unread (413). */
export const REPORT_MAX_BYTES = 2048;
export const MESSAGE_MAX = 300;
export const ROUTE_MAX = 200;

export type Release = { commit: string; deployment: string; environment: string };

export type ErrorLine = {
  event: "app_error";
  source: "server" | "browser";
  commit: string;
  deployment: string;
  environment: string;
  /** A path or route pattern: no query, no fragment, redacted. */
  route: string;
  /** Next's error digest (`<hash>` or `<hash>@E<code>`), or null. */
  digest: string | null;
  /** Browser reports only, redacted and capped; server lines carry none (Next logs its own). */
  message: string | null;
};

/** Everything a message might carry that is not ours to log, most specific first. */
const REDACTIONS: readonly [RegExp, string][] = [
  [/\b[a-z][a-z0-9+.-]*:\/\/\S+/gi, "[url]"],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[email]"],
  [/\bsk-[A-Za-z0-9_-]{4,}/g, "[key]"],
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?/g, "[token]"],
  [/\b(bearer|basic)\s+\S+/gi, "$1 [token]"],
  [/\b(password|passwd|secret|token|api[_-]?key|apikey|authorization|cookie)\b(\s*[:=]\s*)\S+/gi, "$1$2[redacted]"],
  // Long opaque runs: hex secrets, base64 keys, token hashes, UUIDs (user and request ids).
  [/[A-Za-z0-9+/=_-]{32,}/g, "[token]"],
];

/** `text` with every secret-shaped run replaced, control characters flattened, capped at `max`. */
export function redact(text: string, max: number): string {
  let out = text.replace(/[\u0000-\u001f\u007f]+/g, " ");
  for (const [pattern, replacement] of REDACTIONS) out = out.replace(pattern, replacement);
  return out.length > max ? `${out.slice(0, max)}…` : out;
}

const DIGEST = /^\d{1,20}(?:@E\d{1,6})?$/;
/** Next's digest shape only; anything else (a smuggled value) is dropped, never logged. */
export const safeDigest = (value: unknown): string | null =>
  typeof value === "string" && DIGEST.test(value) ? value : null;

/** A path with its query and fragment removed (a callback's `token_hash` lives there), redacted. */
export function safeRoute(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/")) return "unknown";
  // Segment by segment, so a long path is not one opaque run; an id segment reads `[token]`.
  const path = value.split(/[?#]/, 1)[0].split("/").map((segment) => redact(segment, ROUTE_MAX)).join("/");
  return path.length > ROUTE_MAX ? `${path.slice(0, ROUTE_MAX)}…` : path;
}

function line(source: ErrorLine["source"], release: Release, route: string, digest: string | null, message: string | null): ErrorLine {
  const { commit, deployment, environment } = release;
  return { event: "app_error", source, commit, deployment, environment, route, digest, message };
}

/** instrumentation.ts `onRequestError`: the route pattern (`/usage/[requestId]`, no ids) and the digest. */
export function serverErrorLine(error: unknown, routePath: string, release: Release): ErrorLine {
  const digest = typeof error === "object" && error !== null && "digest" in error ? error.digest : undefined;
  return line("server", release, safeRoute(routePath), safeDigest(digest), null);
}

export type Parsed = { status: 204; line: ErrorLine } | { status: 400 | 413 | 415 };

/** POST /api/client-errors: a small JSON `{digest, route, message}` → one line, or a refusal status. */
export function parseClientReport(contentType: string | null, body: string, release: Release): Parsed {
  if (!/^application\/json\s*(?:;|$)/i.test(contentType ?? "")) return { status: 415 };
  if (new TextEncoder().encode(body).length > REPORT_MAX_BYTES) return { status: 413 };
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    return { status: 400 };
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { status: 400 };
  const { digest, route, message } = raw as Record<string, unknown>;
  const text = typeof message === "string" ? redact(message, MESSAGE_MAX) : null;
  return { status: 204, line: line("browser", release, safeRoute(route), safeDigest(digest), text) };
}

/** What an error page sends: bounded client-side too, so a report never nears the limit. */
export function browserReport(error: { message?: unknown; digest?: unknown }, pathname: string) {
  return {
    digest: safeDigest(error.digest),
    route: pathname.slice(0, ROUTE_MAX),
    message: typeof error.message === "string" ? error.message.slice(0, MESSAGE_MAX) : null,
  };
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
