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
 * A fresh fake per request: it is deterministic, so two requests render the same rows, and a
 * mutation in one request cannot leak into another.
 */
export function consoleContext(env: { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string } = process.env): ConsoleContext | null {
  if (env.INFRX_CONSOLE_PREVIEW !== "1" || !["development", "test"].includes(env.NODE_ENV ?? "")) {
    return null;
  }
  const services = createFakeConsoleServices();
  return {
    services,
    session: services.sessions.owner,
    // The fake's clock is frozen (documented fake-only behaviour); a real provider uses `new Date()`.
    now: new Date(orgsFixture.clock),
  };
}
