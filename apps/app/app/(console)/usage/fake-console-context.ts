/** Development-only fixture context. C0 supplies the authenticated database context at integration. */
import { createFakeConsoleServices } from "../../../lib/contracts/fake-services.ts";
import type { ConsoleServices } from "../../../lib/contracts/services.ts";
import type { SessionContext } from "../../../lib/contracts/types.ts";
import orgsFixture from "../../../lib/contracts/fixtures/orgs.json" with { type: "json" };

export type ConsoleContext = {
  services: ConsoleServices;
  session: SessionContext;
  now: Date;
};

/**
 * The gate a production build cannot be talked out of (S1-fix B2).
 *
 * Next inlines only the *textual* `process.env.NODE_ENV`, so a check that reads `NODE_ENV` off an
 * object survives into the bundle as a run-time lookup: `next build` followed by
 * `NODE_ENV=development INFRX_CONSOLE_PREVIEW=1 next start` then served fixture accounts. Written
 * this way the comparison is folded at build time and the whole fixture path is dropped from the
 * production chunk. The injected-environment parameter below stays for the tests.
 */
const PRODUCTION_BUILD = process.env.NODE_ENV === "production";

/**
 * A fresh fake per request: it is deterministic, so two requests render the same rows, and a
 * mutation in one request cannot leak into another.
 */
export type PreviewEnv = { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string };

/** The one gate for every console fixture (the v1 services here, the CREDIT reads in U1R). */
export function previewAllowed(env: PreviewEnv = process.env): boolean {
  if (PRODUCTION_BUILD) return false;
  return env.INFRX_CONSOLE_PREVIEW === "1" && ["development", "test"].includes(env.NODE_ENV ?? "");
}

export function consoleContext(env: PreviewEnv = process.env): ConsoleContext | null {
  if (!previewAllowed(env)) return null;
  const services = createFakeConsoleServices();
  return {
    services,
    session: services.sessions.owner,
    // The fake's clock is frozen (documented fake-only behaviour); a real provider uses `new Date()`.
    now: new Date(orgsFixture.clock),
  };
}
