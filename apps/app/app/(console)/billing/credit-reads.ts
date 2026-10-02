/**
 * The consumer CREDIT reads the Usage and Credits pages render (U1R), over infrx-api (AP-09 09a).
 *
 * Every read is a `/console/v1/*` route through the generated client as the signed-in user (no
 * service key, no caller-supplied tenant): `credits` (the wallet, with the API's own `spent`),
 * `credit-ledger`, `legacy-statement`, `requests` (narrowed by model, key and the half-open window
 * [from, to)) and `keys`. The API pages with its opaque cursors and states every figure; this
 * module only checks each document is exactly what the contract says.
 *
 * Nothing here throws and nothing here guesses: an error, a transport failure or a document that
 * is not exact is a typed failure the page renders as unavailable, never a zero or a plausible
 * balance. Money stays a decimal string from the wire to the screen; nothing is derived here.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so `node --test` loads this.
 */

import type { components } from "@infrx/api-client/consumer";
import type { ConsumerApi } from "../../../lib/api/index.ts";
import { amountIn, credit, fail, instant, Malformed, optionalInstant, read, usd } from "../../../lib/api/result.ts";
import { unitOfRegime, type AccountingRegime, type Credit, type Usd } from "../../../lib/contracts/v2/money-units.ts";
import { MAX_PAGE_LIMIT, type Page, type Result } from "../../../lib/contracts/types.ts";

type S = components["schemas"];

// ---------------------------------------------------------------------------
// DTOs
// ---------------------------------------------------------------------------

export type CreditWallet = {
  walletId: string;
  ledgerTotal: Credit;
  reservedTotal: Credit;
  /** As the API stated it; the view model checks it against total - reserved. */
  available: Credit;
  /** What settled requests have drawn, as the API states it; null when it cannot say (never a guess). */
  spent: Credit | null;
  /** When the one-time signup grant landed, or null when it has not. */
  signupGrantedAt: string | null;
};

export type CreditLedgerEntry = {
  id: string;
  createdAt: string;
  kind: string;
  amount: Credit;
  requestId: string | null;
  reason: string;
};

export type LegacyUsd = { balance: Usd; entryCount: number; rolloutHold: boolean };

export type HoldState = "held" | "settled" | "released" | "unknown";

export type ConsumerJob = {
  requestId: string;
  createdAt: string;
  requestedModel: string;
  modelRevision: string;
  executionMode: string;
  state: string;
  outcomeCause: string | null;
  regime: AccountingRegime;
  unit: "CREDIT" | "USD";
  settlementState: string | null;
  usageCertainty: string | null;
  promptTokens: number | null;
  completionTokens: number | null;
  /** The job's reservation in its own unit, and where it stands. */
  hold: string | null;
  holdState: HoldState | null;
  /** What settlement charged, in the job's unit; null until it settled (never a zero). */
  charged: string | null;
  resultAvailable: boolean;
  resultExpiresAt: string | null;
};

export type PageRequest = { limit: number; cursor: string | null };

/**
 * `consumer_jobs`' 0024 filters. Unset (null or absent) is no filter and is not sent, so an
 * unfiltered call is exactly 0021's. `from`/`to` are instants: the window is [from, to).
 */
export type JobsRequest = PageRequest & {
  model?: string | null;
  keyId?: string | null;
  from?: string | null;
  to?: string | null;
};

/** A key the individual can filter their requests by. A job row names no key (0024 note 3). */
export type KeyOption = { id: string; name: string; prefix: string };

export interface CreditReads {
  /** The caller's consumer wallet; `null` when none exists yet (no grant). */
  wallet(): Promise<Result<CreditWallet | null>>;
  /** The caller's own ledger: the API takes the wallet from the session, never an argument. */
  ledger(page: PageRequest): Promise<Result<Page<CreditLedgerEntry>>>;
  legacyUsd(): Promise<Result<LegacyUsd>>;
  jobs(page: JobsRequest): Promise<Result<Page<ConsumerJob>>>;
  /** The caller's own keys (revoked ones too: their requests are still listed). */
  keys(): Promise<Result<KeyOption[]>>;
}

/** How many keys the filter offers; a consumer has a handful. */
export const KEYS_BOUND = 100;


// ---------------------------------------------------------------------------
// Document readers: fail closed. Anything not exactly as promised throws, and `read` maps it.
// ---------------------------------------------------------------------------

const HOLD_STATES: readonly HoldState[] = ["held", "settled", "released", "unknown"];

function walletOf(c: S["Credits"]): CreditWallet | null {
  if (c.wallet_id === null) return null;
  return {
    walletId: c.wallet_id,
    ledgerTotal: credit(c.ledger_total),
    reservedTotal: credit(c.reserved_total),
    available: credit(c.available),
    spent: c.spent === null ? null : credit(c.spent),
    signupGrantedAt: optionalInstant(c.signup_granted_at),
  };
}

function entryOf(e: S["LedgerEntry"]): CreditLedgerEntry {
  return {
    id: e.entry_id,
    createdAt: instant(e.created_at),
    kind: e.kind,
    amount: credit(e.amount),
    requestId: e.request_id,
    reason: e.reason,
  };
}

export function jobOf(r: S["RequestSummary"]): ConsumerJob {
  const regime = r.accounting_regime;
  // The unit is the regime's; an amount in any other unit is refused, never relabelled.
  const unit = unitOfRegime(regime) as "CREDIT" | "USD";
  if (r.hold_state !== null && !(HOLD_STATES as readonly string[]).includes(r.hold_state)) {
    throw new Malformed("hold_state is outside its vocabulary");
  }
  return {
    requestId: r.request_id,
    createdAt: instant(r.created_at),
    requestedModel: r.requested_model,
    modelRevision: r.model_revision,
    executionMode: r.execution_mode,
    state: r.state,
    outcomeCause: r.outcome_cause,
    regime: regime as AccountingRegime,
    unit,
    settlementState: r.settlement_state,
    usageCertainty: r.usage_certainty,
    promptTokens: r.prompt_tokens,
    completionTokens: r.completion_tokens,
    hold: amountIn(r.hold, unit),
    holdState: r.hold_state as HoldState | null,
    charged: amountIn(r.charged, unit),
    resultAvailable: r.result_access === "available",
    resultExpiresAt: optionalInstant(r.result_expires_at),
  };
}

/** A page as the API takes it: 1..100 rows, refused before the call; the cursor passed back as given. */
function pageQuery(page: PageRequest): { limit: number; cursor?: string } | null {
  if (!Number.isInteger(page.limit) || page.limit < 1 || page.limit > MAX_PAGE_LIMIT) return null;
  return page.cursor ? { limit: page.limit, cursor: page.cursor } : { limit: page.limit };
}

const BAD_PAGE = `A page holds 1 to ${MAX_PAGE_LIMIT} rows.`;
const pageOf = <W, T>(map: (w: W) => T) => (p: { data: W[]; next_cursor?: string | null }): Page<T> => ({
  items: p.data.map(map),
  next_cursor: p.next_cursor ?? null,
});

/** Only the filters that are set, each to its own query parameter. */
function jobFilters(page: JobsRequest): Record<string, string> {
  const query: Record<string, string> = {};
  if (page.model) query.model = page.model;
  if (page.keyId) query.key_id = page.keyId;
  if (page.from) query.from = page.from;
  if (page.to) query.to = page.to;
  return query;
}

export function apiCreditReads(api: ConsumerApi): CreditReads {
  return {
    wallet: () => read(api.call("get", "/console/v1/credits"), walletOf),

    async ledger(page) {
      const query = pageQuery(page);
      if (query === null) return fail("invalid_request", BAD_PAGE);
      return read(api.call("get", "/console/v1/credit-ledger", { query }), pageOf(entryOf));
    },

    legacyUsd: () =>
      read(api.call("get", "/console/v1/legacy-statement"), (s) => ({ balance: usd(s.balance), entryCount: s.entry_count, rolloutHold: s.rollout_hold })),

    async jobs(page) {
      const query = pageQuery(page);
      if (query === null) return fail("invalid_request", BAD_PAGE);
      return read(api.call("get", "/console/v1/requests", { query: { ...query, ...jobFilters(page) } }), pageOf(jobOf));
    },

    keys: () =>
      read(api.call("get", "/console/v1/keys", { query: { limit: KEYS_BOUND } }), (p) => p.data.map((k) => ({ id: k.id, name: k.name, prefix: k.prefix }))),
  };
}
