/**
 * C0 over infrx-api (AP-09 09a): the signed-in individual's consumer context and read port.
 *
 * The account is `GET /console/v1/me` (verified, onboarding, suspended - the server's, never a
 * guess) and every read is a `/console/v1/*` route through the generated client with the user's
 * own session as the Bearer. Nothing here holds a credential, a cursor secret or a fixture: the API
 * pages with its own opaque cursors and states every figure; a read that cannot answer returns a
 * non-ok `Result`, and the page renders "unavailable".
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so `node --test` loads this.
 */

import type { components } from "@infrx/api-client/consumer";
import type { ConsumerApi } from "../api/index.ts";
import { credit, fail, instant, optionalInstant, read, usd, amountIn, Malformed } from "../api/result.ts";
import { DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, type ApiKeySummary, type PageQuery, type Result } from "../contracts/types.ts";
import { LEDGER_ENTRY_KINDS_V2, type LedgerEntryKindV2 } from "../contracts/v2/types.ts";
import { unitOfRegime, type AccountingRegime } from "../contracts/v2/money-units.ts";
import type {
  ConsoleShell,
  ConsumerContext,
  ConsumerReads,
  ConsumerRequest,
  ConsumerSession,
  CreditLedgerEntry,
} from "../contracts/v2/consumer.ts";

// The port types are contracts (C0 WR-3, lib/contracts/v2/consumer.ts); re-exported for existing importers.
export type {
  AuthUser,
  ConsoleShell,
  ConsumerAccount,
  ConsumerContext,
  ConsumerReads,
  ConsumerRequest,
  ConsumerSession,
  CreditLedgerEntry,
} from "../contracts/v2/consumer.ts";

type S = components["schemas"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NOT_FOUND = "no such request for this account";

/**
 * The context from `/console/v1/me` (and, for a ready account, its wallet id from
 * `/console/v1/credits`). 401 is signed out; anything else that is not an answer - an outage, a
 * malformed document - is `unavailable`, never signed out (a login page cannot help) and never
 * onboarding (a second grant flow).
 */
export async function consumerContextFrom(api: ConsumerApi, email: string): Promise<ConsumerContext> {
  try {
    const [me, credits] = await Promise.all([api.call("get", "/console/v1/me"), api.call("get", "/console/v1/credits")]);
    if (!me.ok) return me.error.status === 401 ? { state: "signed_out" } : { state: "unavailable" };
    const { actor, state, suspended } = me.data;
    const userId = actor.user_id ?? "";
    if (!UUID.test(userId)) return { state: "unavailable" };
    if (state === "unverified" || state === "onboarding") return { state, userId, email };
    if (state !== "ready" || !credits.ok) return { state: "unavailable" };
    const orgId = actor.org_id ?? "";
    const walletId = credits.data.wallet_id ?? "";
    if (!UUID.test(orgId) || !UUID.test(walletId) || typeof suspended !== "boolean") return { state: "unavailable" };
    return { state: "ready", account: { userId, email, walletId, orgId, suspended } };
  } catch {
    return { state: "unavailable" };
  }
}

export function keyOf(key: S["KeySummary"]): ApiKeySummary {
  return {
    id: key.id,
    name: key.name,
    prefix: key.prefix,
    created_at: instant(key.created_at),
    last_used_at: optionalInstant(key.last_used_at),
    revoked_at: optionalInstant(key.revoked_at),
    // The API states no capture mode on the summary; null is **off** (`traceModeOf`), never a guess.
    trace_mode: null,
  };
}

export function consumerRequestOf(row: S["RequestSummary"]): ConsumerRequest {
  const regime = row.accounting_regime as AccountingRegime;
  const unit = unitOfRegime(regime) as "CREDIT" | "USD";
  if (unit !== "CREDIT" && unit !== "USD") throw new Malformed("unit");
  const { prompt_tokens: prompt, completion_tokens: completion } = row;
  if ((prompt === null) !== (completion === null)) throw new Malformed("half a usage report");
  return {
    request_id: row.request_id,
    created_at: instant(row.created_at),
    model: row.requested_model,
    model_revision: row.model_revision,
    execution_mode: row.execution_mode,
    state: row.state,
    outcome_cause: row.outcome_cause,
    accounting_regime: regime,
    unit,
    hold: amountIn(row.hold, unit) as ConsumerRequest["hold"],
    hold_state: row.hold_state,
    charged: amountIn(row.charged, unit) as ConsumerRequest["charged"],
    settlement_state: row.settlement_state,
    usage_certainty: row.usage_certainty,
    usage: prompt === null || completion === null ? null : { prompt_tokens: prompt, completion_tokens: completion },
    result: row.result_access,
    result_expires_at: optionalInstant(row.result_expires_at),
    // The API's summary carries no settlement instant; unknown is null, never a guess.
    settled_at: null,
  };
}

function ledgerEntryOf(row: S["LedgerEntry"]): CreditLedgerEntry {
  if (!(LEDGER_ENTRY_KINDS_V2 as readonly string[]).includes(row.kind)) throw new Malformed("unknown ledger kind");
  return {
    entry_id: row.entry_id,
    created_at: instant(row.created_at),
    kind: row.kind as LedgerEntryKindV2,
    amount: credit(row.amount),
    request_id: row.request_id,
    reason: row.reason,
  };
}

/** A page query as the API takes it: 1..100 rows (refused before any call), the cursor as given. */
function pageArgs(query: PageQuery | undefined): { limit: number; cursor?: string } | Result<never> {
  const limit = query?.limit ?? DEFAULT_PAGE_LIMIT;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) return fail("invalid_request", `limit must be 1 to ${MAX_PAGE_LIMIT}`);
  return query?.cursor ? { limit, cursor: query.cursor } : { limit };
}

const isFailure = (value: object): value is Result<never> => "ok" in value;

/** The reads for one ready account, every one a `/console/v1/*` route as the signed-in user. */
export function apiConsumerReads(api: ConsumerApi, account: { orgId: string; walletId: string }): ConsumerReads {
  return {
    balance: () =>
      read(api.call("get", "/console/v1/credits"), (c) => {
        if (c.wallet_id !== account.walletId) throw new Malformed("not this account's wallet");
        return {
          schema_version: 2 as const,
          wallet_id: account.walletId,
          kind: "consumer" as const,
          unit: "CREDIT" as const,
          ledger_total: credit(c.ledger_total),
          reserved_total: credit(c.reserved_total),
          available: credit(c.available),
        };
      }),
    legacyUsd: () =>
      read(api.call("get", "/console/v1/legacy-statement"), (s) =>
        s.entry_count === 0
          ? null
          : { schema_version: 2 as const, org_id: account.orgId, balance: usd(s.balance), entry_count: s.entry_count, as_of: instant(s.as_of), rollout_hold: s.rollout_hold },
      ),
    async ledger(query) {
      const args = pageArgs(query);
      if (isFailure(args)) return args;
      return read(api.call("get", "/console/v1/credit-ledger", { query: args }), (p) => ({ items: p.data.map(ledgerEntryOf), next_cursor: p.next_cursor ?? null }));
    },
    async requests(query) {
      const args = pageArgs(query);
      if (isFailure(args)) return args;
      return read(api.call("get", "/console/v1/requests", { query: args }), (p) => ({ items: p.data.map(consumerRequestOf), next_cursor: p.next_cursor ?? null }));
    },
    async request(requestId) {
      if (typeof requestId !== "string" || !UUID.test(requestId)) return fail("not_found", NOT_FOUND);
      return read(api.call("get", "/console/v1/requests/{request_id}", { params: { request_id: requestId } }), consumerRequestOf, NOT_FOUND);
    },
    async result(requestId) {
      if (typeof requestId !== "string" || !UUID.test(requestId)) return fail("not_found", NOT_FOUND);
      return read(api.call("get", "/console/v1/requests/{request_id}/result", { params: { request_id: requestId } }), (r) => r.text);
    },
    keys: () => read(api.call("get", "/console/v1/keys", { query: { limit: MAX_PAGE_LIMIT } }), (p) => p.data.map(keyOf)),
  };
}

/**
 * `consumerSession()` without React's `cache` or the cookie, so it is testable (R48): the context,
 * and the reads only for a ready account. Any failure is `unavailable`.
 */
export async function consumerSessionFrom(api: ConsumerApi, email: string): Promise<ConsumerSession> {
  const context = await consumerContextFrom(api, email);
  if (context.state !== "ready") return { context, reads: null };
  return { context, reads: apiConsumerReads(api, context.account) };
}

/**
 * The shell's decision. An operator is not a consumer: operator access never waits on a consumer
 * wallet, so an operator whose consumer state is onboarding gets the page (no reads) and reaches
 * /admin, which checks the role itself. A session the API no longer accepts goes to `/auth/expired`,
 * which clears the cookie before the login page (a stale cookie would otherwise bounce between
 * /login and the console).
 */
export function consoleShell(
  session: ConsumerSession,
  isOperator: boolean,
  routes: { verifyEmail: string | null; onboarding: string | null },
): ConsoleShell {
  const { context, reads } = session;
  switch (context.state) {
    case "signed_out":
      return { kind: "redirect", to: "/auth/expired" };
    case "unverified":
      return routes.verifyEmail === null ? { kind: "panel", state: "unverified" } : { kind: "redirect", to: routes.verifyEmail };
    case "onboarding":
      if (isOperator) return { kind: "render", email: context.email, reads: null };
      return routes.onboarding === null ? { kind: "panel", state: "onboarding" } : { kind: "redirect", to: routes.onboarding };
    case "ready":
      return reads === null ? { kind: "panel", state: "unavailable" } : { kind: "render", email: context.account.email, reads };
    default:
      return { kind: "panel", state: "unavailable" };
  }
}

/**
 * Provider routes (/dedicated, /teams) until they move to the Lab (/traces moved in V1M). Brief 04 keeps them out of
 * consumer navigation AND protected: anything but an operator flag of exactly `true` is a 404, thrown
 * before the page reads anything (E3A F-2). `notFound` is Next's, passed in so this stays node-loadable.
 */
export function providerRoute(session: { isOperator: boolean }, notFound: () => never): void {
  if (session.isOperator !== true) notFound();
}
