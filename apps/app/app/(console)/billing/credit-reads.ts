/**
 * The consumer CREDIT reads the Usage and Credits pages render (U1R), over PostgREST.
 *
 * Every read goes through the signed-in user's own Supabase session (anon key + user JWT, RLS on):
 * no service key, no caller-supplied tenant. The relations are D1R/D10's consumer read surface:
 *
 * - `console_credit_wallets` (0008): the caller's consumer wallet, filtered by `owner_user_id` — an
 *   operator session sees every wallet through the view, so the filter is what scopes it;
 * - `consumer_credit_ledger` (0024): the caller's own ledger through the JWT subject, one range of
 *   `credit_ledger_wallet_created_idx` stopped by its LIMIT, on D10's opaque cursor;
 * - `console_credit_ledger` (0008): the wallet's grant and adjustments for "Spent", through 0024's
 *   partial index `credit_ledger_wallet_credits_in_idx`;
 * - `consumer_jobs` (0021/0024): the caller's jobs through the JWT subject, keyset-paged on
 *   `jobs_org_created_idx`, money as text in each job's own unit, optionally narrowed by model,
 *   key and the half-open window [from, to);
 * - `api_keys` (0001, RLS): the personal organization's key names, for the key filter;
 * - `console_legacy_usd_statement` (0008): the historical USD balance, its own unit.
 *
 * Nothing here throws and nothing here guesses: an error, a transport failure or a row that is not
 * exactly what the relation promises is a typed failure the page renders as unavailable, never a
 * zero or a plausible balance. Money stays a decimal string from the row to the screen.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so `node --test` loads this.
 */

import {
  parseCredit,
  parseUsd,
  subCredit,
  totalCredit,
  unitOfRegime,
  type AccountingRegime,
  type Credit,
  type Usd,
} from "../../../lib/contracts/v2/money-units.ts";
import { MAX_PAGE_LIMIT, type ErrorCode, type Page, type Result } from "../../../lib/contracts/types.ts";

// ---------------------------------------------------------------------------
// The slice of supabase-js this adapter calls. `lib/supabase/server.ts`'s client satisfies it.
// ---------------------------------------------------------------------------

export type Answer = { data: unknown; error: { code?: string | null; message?: string | null } | null };

export interface Filter extends PromiseLike<Answer> {
  eq(column: string, value: string): Filter;
  neq(column: string, value: string): Filter;
  order(column: string, options: { ascending: boolean }): Filter;
  limit(count: number): Filter;
}

export interface CreditClient {
  from(relation: string): { select(columns: string): Filter };
  rpc(fn: string, args: Record<string, unknown>): PromiseLike<Answer>;
}

// ---------------------------------------------------------------------------
// DTOs
// ---------------------------------------------------------------------------

export type CreditWallet = {
  walletId: string;
  /** The wallet's personal organization (R66): the tenant the legacy statement is read for. */
  orgId: string;
  ledgerTotal: Credit;
  reservedTotal: Credit;
  /** As the database computed it; the view model checks it against total - reserved. */
  available: Credit;
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
  /** The caller's own ledger: the database takes the wallet from the JWT, never an argument. */
  ledger(page: PageRequest): Promise<Result<Page<CreditLedgerEntry>>>;
  /** Σ of the non-debit entries (grant + adjustments); `null` past `CREDITS_IN_BOUND` entries. */
  creditsIn(walletId: string): Promise<Result<Credit | null>>;
  legacyUsd(orgId: string): Promise<Result<LegacyUsd>>;
  jobs(page: JobsRequest): Promise<Result<Page<ConsumerJob>>>;
  /** The personal organization's keys (revoked ones too: their requests are still listed). */
  keys(orgId: string): Promise<Result<KeyOption[]>>;
}

/** How many keys the filter offers; a consumer has a handful. */
export const KEYS_BOUND = 100;

/**
 * How many grant/adjustment entries `creditsIn` will sum. A consumer wallet has one signup grant and
 * the rare operator adjustment, so this is never reached in practice; past it, "spent" is shown as
 * unavailable rather than computed from a partial sum.
 *
 * Spent is derived as Σ(non-debit) − ledger_total; 0024's partial index
 * `credit_ledger_wallet_credits_in_idx` (U1R WR-3(b)) serves the read, so it no longer filters every
 * entry of the wallet by kind.
 */
export const CREDITS_IN_BOUND = 100;

// ---------------------------------------------------------------------------
// Row readers: fail closed. Anything not exactly as promised throws, and `guard` maps it.
// ---------------------------------------------------------------------------

class Malformed extends Error {}

function field(row: unknown, name: string): unknown {
  if (typeof row !== "object" || row === null || !Object.hasOwn(row, name)) {
    throw new Malformed(`the read did not return ${name}`);
  }
  return (row as Record<string, unknown>)[name];
}

function text(row: unknown, name: string): string {
  const value = field(row, name);
  if (typeof value !== "string") throw new Malformed(`${name} must be text`);
  return value;
}

function optionalText(row: unknown, name: string): string | null {
  return field(row, name) === null ? null : text(row, name);
}

/** PostgREST renders `timestamptz` as `…+00:00`; the DTO carries one comparable form. */
const INSTANT = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

function instant(row: unknown, name: string): string {
  const match = INSTANT.exec(text(row, name));
  if (match === null) throw new Malformed(`${name} must be a UTC timestamp`);
  return `${match[1]}.${(match[2] ?? "").padEnd(6, "0")}Z`;
}

function optionalInstant(row: unknown, name: string): string | null {
  return field(row, name) === null ? null : instant(row, name);
}

/** Money is text (R59-9): a JSON number already went through a double and is refused. */
function credit(row: unknown, name: string): Credit {
  return parseCredit(text(row, name));
}

function optionalInteger(row: unknown, name: string): number | null {
  const value = field(row, name);
  if (value === null) return null;
  if (!Number.isSafeInteger(value)) throw new Malformed(`${name} must be an integer`);
  return value as number;
}

function flag(row: unknown, name: string): boolean {
  const value = field(row, name);
  if (typeof value !== "boolean") throw new Malformed(`${name} must be a boolean`);
  return value;
}

function rows(data: unknown): unknown[] {
  if (!Array.isArray(data)) throw new Malformed("the read did not return rows");
  return data;
}

const HOLD_STATES: readonly HoldState[] = ["held", "settled", "released", "unknown"];

function walletOf(row: unknown): CreditWallet {
  return {
    walletId: text(row, "wallet_id"),
    orgId: text(row, "org_id"),
    ledgerTotal: credit(row, "ledger_total"),
    reservedTotal: credit(row, "reserved_total"),
    available: credit(row, "available"),
    signupGrantedAt: optionalInstant(row, "signup_granted_at"),
  };
}

function entryOf(row: unknown): CreditLedgerEntry {
  if (text(row, "unit") !== "CREDIT") throw new Malformed("a CREDIT ledger entry must be CREDIT");
  return {
    id: text(row, "entry_id"),
    createdAt: instant(row, "created_at"),
    kind: text(row, "kind"),
    amount: credit(row, "amount"),
    requestId: optionalText(row, "request_id"),
    reason: text(row, "reason"),
  };
}

function jobOf(row: unknown): ConsumerJob {
  const regime = text(row, "accounting_regime");
  const unit = unitOfRegime(regime);
  // The unit is stated twice, by the regime and by the row; they must agree (no relabelling).
  if (text(row, "unit") !== unit) throw new Malformed(`a ${regime} job cannot be denominated otherwise`);
  const exact = (name: string): string | null => {
    const value = optionalText(row, name);
    if (value === null) return null;
    return unit === "CREDIT" ? parseCredit(value) : parseUsd(value);
  };
  const holdState = optionalText(row, "hold_state");
  if (holdState !== null && !(HOLD_STATES as readonly string[]).includes(holdState)) {
    throw new Malformed("hold_state is outside its vocabulary");
  }
  return {
    requestId: text(row, "request_id"),
    createdAt: instant(row, "created_at"),
    requestedModel: text(row, "requested_model"),
    modelRevision: text(row, "model_revision"),
    executionMode: text(row, "execution_mode"),
    state: text(row, "state"),
    outcomeCause: optionalText(row, "outcome_cause"),
    regime: regime as AccountingRegime,
    unit: unit as "CREDIT" | "USD",
    settlementState: optionalText(row, "settlement_state"),
    usageCertainty: optionalText(row, "usage_certainty"),
    promptTokens: optionalInteger(row, "prompt_tokens"),
    completionTokens: optionalInteger(row, "completion_tokens"),
    hold: exact("hold"),
    holdState: holdState as HoldState | null,
    charged: exact("charged"),
    resultAvailable: flag(row, "result_available"),
    resultExpiresAt: optionalInstant(row, "result_expires_at"),
  };
}

// ---------------------------------------------------------------------------
// Errors: a code the page can explain, a message that carries no internals.
// ---------------------------------------------------------------------------

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

function refusal<T>(error: NonNullable<Answer["error"]>): Result<T> {
  if (error.code === "42501") return fail("forbidden", "This account cannot read that.");
  if (error.code === "P0001" && (error.message ?? "").startsWith("invalid_cursor")) {
    return fail("invalid_cursor", "This page link is no longer valid.");
  }
  // Everything else — PostgREST down, a JWT expired, a function missing — is "not now".
  return fail("dependency_unavailable", "Account data is unavailable right now.");
}

async function read<T>(query: PromiseLike<Answer>, map: (data: unknown) => T): Promise<Result<T>> {
  let answer: Answer;
  try {
    answer = await query;
  } catch {
    return fail("dependency_unavailable", "Account data is unavailable right now.");
  }
  if (answer.error !== null && answer.error !== undefined) return refusal(answer.error);
  try {
    return { ok: true, value: map(answer.data) };
  } catch {
    return fail("internal_error", "Account data could not be read exactly.");
  }
}

// ---------------------------------------------------------------------------
// Keyset pages
// ---------------------------------------------------------------------------

/**
 * One page of a D10 `consumer_*` read, on C0's rule (R146): a limit outside 1..100 is refused before
 * the call; otherwise `limit + 1` rows are asked, clamped to 0024's cap of 100, and the extra row only
 * decides whether there is a next page - at the cap a full page carries a cursor (the next page may
 * be empty). The next page resumes from D10's opaque cursor on the last row shown: a bound
 * parameter, never spliced into a filter; a malformed one is D10's `invalid_cursor`.
 */
async function rpcPage<T>(
  page: PageRequest,
  call: (ask: number) => PromiseLike<Answer>,
  parse: (row: unknown) => T,
): Promise<Result<Page<T>>> {
  const { limit } = page;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
    return fail("invalid_request", `A page holds 1 to ${MAX_PAGE_LIMIT} rows.`);
  }
  const ask = Math.min(limit + 1, MAX_PAGE_LIMIT);
  return read(call(ask), (data) => {
    const found = rows(data);
    const cursors = found.map((row) => text(row, "cursor"));
    const items = found.map(parse);
    const shown = items.slice(0, limit);
    const more = items.length > limit || (ask === limit && items.length === limit);
    return { items: shown, next_cursor: more && shown.length > 0 ? cursors[shown.length - 1] : null };
  });
}

/** Only the filters that are set; the RPC's named parameters, each to its own. */
function jobFilterArgs(page: JobsRequest): Record<string, string> {
  const args: Record<string, string> = {};
  if (page.model) args.p_model = page.model;
  if (page.keyId) args.p_key_id = page.keyId;
  if (page.from) args.p_from = page.from;
  if (page.to) args.p_to = page.to;
  return args;
}

export function postgrestCreditReads(client: CreditClient, userId: string): CreditReads {
  return {
    wallet: () =>
      read(
        client
          .from("console_credit_wallets")
          .select("wallet_id, org_id, ledger_total, reserved_total, available, signup_granted_at")
          .eq("owner_user_id", userId)
          .eq("kind", "consumer")
          .limit(2),
        (data) => {
          const found = rows(data);
          if (found.length > 1) throw new Malformed("more than one consumer wallet");
          return found.length === 0 ? null : walletOf(found[0]);
        },
      ),

    // C0 WR-5 / U1R WR-3(c) (0024): O(limit) however deep the cursor; no actor column at all.
    ledger: (page) =>
      rpcPage(page, (ask) => client.rpc("consumer_credit_ledger", { p_after: page.cursor, p_limit: ask }), entryOf),

    creditsIn: (walletId) =>
      read(
        client
          .from("console_credit_ledger")
          .select("amount")
          .eq("wallet_id", walletId)
          .neq("kind", "inference_debit")
          .limit(CREDITS_IN_BOUND + 1),
        (data) => {
          const found = rows(data);
          return found.length > CREDITS_IN_BOUND ? null : totalCredit(found.map((r) => credit(r, "amount")));
        },
      ),

    legacyUsd: (orgId) =>
      read(client.rpc("console_legacy_usd_statement", { p_org: orgId }), (data) => {
        const [row, ...rest] = rows(data);
        if (row === undefined || rest.length > 0) throw new Malformed("one statement row expected");
        if (text(row, "unit") !== "USD") throw new Malformed("the legacy statement is USD");
        const entryCount = optionalInteger(row, "entry_count");
        if (entryCount === null || entryCount < 0) throw new Malformed("entry_count");
        return { balance: parseUsd(text(row, "balance")), entryCount, rolloutHold: flag(row, "rollout_hold") };
      }),

    jobs: (page) =>
      rpcPage(page, (ask) => client.rpc("consumer_jobs", { p_after: page.cursor, p_limit: ask, ...jobFilterArgs(page) }), jobOf),

    keys: (orgId) =>
      read(
        client
          .from("api_keys")
          .select("id, name, prefix")
          .eq("org_id", orgId)
          .order("created_at", { ascending: false })
          .limit(KEYS_BOUND),
        (data) => rows(data).map((row) => ({ id: text(row, "id"), name: text(row, "name"), prefix: text(row, "prefix") })),
      ),
  };
}

/** Spent = what came in (grant + adjustments) minus what is left on the ledger. Exact. */
export function spentCredit(wallet: CreditWallet, creditsIn: Credit | null): Credit | null {
  return creditsIn === null ? null : subCredit(creditsIn, wallet.ledgerTotal);
}
