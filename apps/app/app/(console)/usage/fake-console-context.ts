/**
 * The one seam where the usage and billing pages get their data (U1).
 *
 * U1 is built against the fixture-backed fake by instruction: C1 implements the same
 * `ConsoleServices` interface over PostgreSQL, and swapping it in is this file and nothing else.
 * Nothing here is a claim that the pages read real data — they read fixtures, and the fake's clock
 * is frozen at the fixture instant, so the ranges the pages compute land on fixture rows.
 *
 * INTEGRATION REQUEST (coordinator): replace this with the real provider once C1 lands —
 * `services` from C1's factory, `session` mapped from `getSession()` (`lib/session.ts`) and `now`
 * from `new Date()`. The pages import only `consoleContext()`.
 */

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
export function consoleContext(): ConsoleContext {
  const services = createFakeConsoleServices();
  return {
    services,
    session: services.sessions.owner,
    // The fake's clock is frozen (documented fake-only behaviour); a real provider uses `new Date()`.
    now: new Date(orgsFixture.clock),
  };
}
