/**
 * The server-only edge of the console services (C1).
 *
 * This is the only module in `lib/services/` that touches the environment or the session cookie, and
 * it refuses to load in a browser bundle the same way `lib/supabase/admin.ts` does — the `server-only`
 * package is not a dependency of this console and F2 froze the dependency set, so the existing guard
 * is reused rather than a new one added. No test imports this file (R48: a module reachable from
 * `node --test` may not pull in `next/*`), which is also why the cursor secret is read here and passed
 * into `createConsoleServices` as a value.
 */

import { getSession } from "../session.ts";
import type { SessionContext } from "../contracts/types.ts";
import { createConsoleServices, type ConsoleServicesConfig } from "./console.ts";
import type { ConsoleServices } from "../contracts/services.ts";

function assertServer(what: string): void {
  if (typeof window !== "undefined") throw new Error(`${what} is server-only`);
}

/** Environment name only. The value never reaches a DTO, a cursor payload, a log or an error. */
export const CURSOR_SECRET_ENV = "CONSOLE_CURSOR_SECRET";

export function consoleCursorSecret(): string {
  assertServer("consoleCursorSecret()");
  const secret = process.env[CURSOR_SECRET_ENV];
  // Fail closed: an unsigned cursor is a caller-writable keyset bound straight into a query.
  if (secret === undefined || secret.length < 16) {
    throw new Error(`${CURSOR_SECRET_ENV} must be set to at least 16 characters`);
  }
  return secret;
}

/**
 * The trusted caller identity. The tenant is whatever the session says and nothing else: no route
 * segment, no form field and no query parameter is consulted, here or downstream.
 */
export async function resolveSessionContext(): Promise<SessionContext> {
  assertServer("resolveSessionContext()");
  const session = await getSession();
  return {
    userId: session.userId,
    email: session.email,
    orgId: session.orgId,
    orgName: session.orgName,
    role: session.role,
    isOperator: session.isOperator,
  };
}

/**
 * Build the services with the injected query ports.
 *
 * The ports themselves are **integration-pending**: the named queries of `./query.ts` are rendered
 * against D1's committed 0003–0005 schema; product-v2 additions belong to D1R. C0 supplies the
 * real Supabase/PostgREST consumer port independently of Lab content/ClickHouse work in C2.
 * The coordinator wires the authenticated context at integration; until then the only executor
 * is the in-memory port the track tests inject.
 */
export function createServerConsoleServices(ports: Omit<ConsoleServicesConfig, "cursorSecret">): ConsoleServices {
  assertServer("createServerConsoleServices()");
  return createConsoleServices({ ...ports, cursorSecret: consoleCursorSecret() });
}
