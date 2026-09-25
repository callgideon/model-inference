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

import { cache } from "react";
import { getSession } from "../session.ts";
import { createClient } from "../supabase/server.ts";
import type { SessionContext } from "../contracts/types.ts";
import {
  authUserOutcome,
  createConsoleServices,
  createConsumerReads,
  resolveConsumerContext,
  type ConsoleServicesConfig,
  type ConsumerContext,
  type ConsumerReads,
} from "./console.ts";
import { postgrestPort, type PostgrestClient } from "./query.ts";
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

/** The consumer App's request context: who is asking, and - only for a ready account - their reads. */
export type ConsumerSession = { context: ConsumerContext; reads: ConsumerReads | null };

const UNAVAILABLE: ConsumerSession = { context: { state: "unavailable" }, reads: null };

/**
 * C0: the signed-in individual's consumer account and read port, once per request.
 *
 * Everything runs as the individual: the Supabase server client carries their cookie JWT, so RLS,
 * the views' guards and D10's `consumer_*` functions decide what is visible. No service key, no
 * customer API key and no fixture is on this path. GoTrue's `getUser()` revalidates the token (the
 * cookie alone is not trusted), the account is found by wallet owner (`resolveConsumerContext`), and
 * any failure - configuration, auth service, database - is the `unavailable` state, never a
 * fixture, a zero or a redirect to sign-in.
 */
export const consumerSession = cache(async (): Promise<ConsumerSession> => {
  assertServer("consumerSession()");
  try {
    const supabase = await createClient();
    const user = authUserOutcome(await supabase.auth.getUser());
    if (user === "unavailable") return UNAVAILABLE;
    const client = supabase as unknown as PostgrestClient;
    const pg = postgrestPort(client);
    const context = await resolveConsumerContext(pg, user);
    if (context.state !== "ready") return { context, reads: null };
    return { context, reads: createConsumerReads({ pg, rpc: client, cursorSecret: consoleCursorSecret() }, context.account) };
  } catch {
    // A missing URL/anon key or cursor secret, or a client that threw instead of answering.
    return UNAVAILABLE;
  }
});
