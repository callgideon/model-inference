/**
 * The development preview's data source (INFRX_CONSOLE_PREVIEW=1): the client fake of infrx-api
 * (`lib/fake-api.ts`), answered through the generated client, so a preview renders exactly what the
 * production adapters make of the documented API.
 */
import type { ConsumerApi } from "../../../lib/api/index.ts";
import { FAKE_CLOCK, fakeConsoleApi } from "../../../lib/fake-api.ts";

export type ConsoleContext = { api: ConsumerApi; now: Date };

/**
 * The gate a production build cannot be talked out of (S1-fix B2).
 *
 * Next inlines only the *textual* `process.env.NODE_ENV`, so a check that reads `NODE_ENV` off an
 * object survives into the bundle as a run-time lookup: `next build` followed by
 * `NODE_ENV=development INFRX_CONSOLE_PREVIEW=1 next start` then served fixture accounts. Written
 * this way the comparison is folded at build time and the fixture path is dropped from the
 * production chunk. The injected-environment parameter below stays for the tests.
 */
const PRODUCTION_BUILD = process.env.NODE_ENV === "production";

/** A fresh fake per request: deterministic, and a change in one request cannot leak into another. */
export function consoleContext(env: { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string } = process.env): ConsoleContext | null {
  if (PRODUCTION_BUILD) return null;
  if (env.INFRX_CONSOLE_PREVIEW !== "1" || !["development", "test"].includes(env.NODE_ENV ?? "")) {
    return null;
  }
  // The fake's clock is frozen (documented fake-only behaviour); a real request uses `new Date()`.
  return { api: fakeConsoleApi(), now: new Date(FAKE_CLOCK) };
}

/** One gate for every console preview: it can never be open for one page and closed for another. */
export function previewAllowed(env: { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string } = process.env): boolean {
  return consoleContext(env) !== null;
}
