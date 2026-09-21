/**
 * Usage view model (U1). Every decision the usage page makes lives here: filter parsing and
 * serialization, keyset pagination state, the loading/empty/error state machine, and the
 * row → display mapping.
 *
 * Rules this file exists to keep:
 * - money is formatted only through the `Money` helpers; `Number` never touches an amount (R11);
 * - an unsettled amount is never presented as a charge — a hold is a hold (02, R13);
 * - a cursor is bound to its query scope, so changing a filter discards it (08 §9);
 * - it is a pure `.ts` module with relative imports, so `node --test` loads it (R48).
 */

import { displayMoney, ZERO_MONEY } from "../../../lib/contracts/money.ts";
import { num } from "../../../lib/format.ts";
import {
  DEFAULT_PAGE_LIMIT,
  ERROR_CODE_HTTP_STATUS,
  type ErrorCode,
  type Result,
  type UsageDay,
  type UsageQuery,
  type UsageRow,
  type UsageSummary,
} from "../../../lib/contracts/types.ts";

// ---------------------------------------------------------------------------
// URL state: what a console URL may say, and nothing else
// ---------------------------------------------------------------------------

/** Next's `searchParams`: a repeated parameter arrives as an array. */
export type SearchParams = Record<string, string | string[] | undefined>;

const MIN = 60_000;

/** Time-range picker values, in milliseconds. */
export const RANGES = {
  "5m": 5 * MIN,
  "15m": 15 * MIN,
  "30m": 30 * MIN,
  "1h": 60 * MIN,
  "6h": 6 * 60 * MIN,
  "24h": 24 * 60 * MIN,
  "7d": 7 * 24 * 60 * MIN,
  "30d": 30 * 24 * 60 * MIN,
} as const;

export type RangeKey = keyof typeof RANGES;

export const DEFAULT_RANGE: RangeKey = "24h";

/** The select value that means "no filter"; an empty string would be ambiguous in a URL. */
export const ALL = "all";

/** One page of rows. The contract's maximum is 100; 25 is the console's page. */
export const PAGE_SIZE = DEFAULT_PAGE_LIMIT;

/**
 * Keyset pagination state. `cursor` is the opaque cursor the service minted for the page being
 * shown; `trail` holds the cursors of the pages before it, oldest first, which is the only way to
 * walk *back* through an opaque keyset cursor — there is nothing in a cursor to decrement.
 *
 * ponytail: the trail rides in the URL, so a deep walk makes a long URL (~60 characters per page
 * with the fixture's cursors). Move it to a server-side page token if a walk ever gets deep enough
 * to matter.
 */
export type PageCursor = {
  cursor: string | null;
  trail: readonly string[];
};

export type UsageFilters = PageCursor & {
  range: RangeKey;
  keyId: string | null;
  model: string | null;
};

function one(value: string | string[] | undefined): string | null {
  const last = Array.isArray(value) ? value[value.length - 1] : value;
  return last === undefined || last === "" ? null : last;
}

function many(value: string | string[] | undefined): string[] {
  if (Array.isArray(value)) return value.filter((entry) => entry !== "");
  return value === undefined || value === "" ? [] : [value];
}

export function parsePageCursor(params: SearchParams): PageCursor {
  return { cursor: one(params.cursor), trail: many(params.trail) };
}

/**
 * The URL is untrusted input, so every value is either recognised or dropped: an unknown range
 * falls back to the default rather than producing an empty page that reads as "no traffic". A key
 * or model the service does not know is passed through — the service validates it and its typed
 * refusal is what the page shows.
 */
export function parseUsageFilters(params: SearchParams): UsageFilters {
  const range = one(params.range);
  const keyId = one(params.key);
  const model = one(params.model);
  return {
    range: range !== null && range in RANGES ? (range as RangeKey) : DEFAULT_RANGE,
    keyId: keyId === ALL ? null : keyId,
    model: model === ALL ? null : model,
    ...parsePageCursor(params),
  };
}

function appendCursor(search: URLSearchParams, state: PageCursor): URLSearchParams {
  if (state.cursor !== null) search.set("cursor", state.cursor);
  for (const cursor of state.trail) search.append("trail", cursor);
  return search;
}

/** Canonical query string: defaults are omitted, so page 1 of an unfiltered view is a bare path. */
export function usageSearch(filters: UsageFilters): string {
  const search = new URLSearchParams();
  if (filters.range !== DEFAULT_RANGE) search.set("range", filters.range);
  if (filters.keyId !== null) search.set("key", filters.keyId);
  if (filters.model !== null) search.set("model", filters.model);
  return appendCursor(search, filters).toString();
}

export function hrefWith(path: string, search: string): string {
  return search === "" ? path : `${path}?${search}`;
}

export function usageHref(filters: UsageFilters, path = "/usage"): string {
  return hrefWith(path, usageSearch(filters));
}

export function ledgerHref(state: PageCursor, path = "/billing"): string {
  return hrefWith(path, appendCursor(new URLSearchParams(), state).toString());
}

// ---------------------------------------------------------------------------
// Pagination and filter transitions
// ---------------------------------------------------------------------------

export function nextCursorState<T extends PageCursor>(state: T, nextCursor: string): T {
  return {
    ...state,
    cursor: nextCursor,
    trail: state.cursor === null ? [] : [...state.trail, state.cursor],
  };
}

export function previousCursorState<T extends PageCursor>(state: T): T {
  const trail = [...state.trail];
  const cursor = trail.pop() ?? null;
  return { ...state, cursor, trail };
}

export function firstCursorState<T extends PageCursor>(state: T): T {
  return { ...state, cursor: null, trail: [] };
}

export function hasPreviousPage(state: PageCursor): boolean {
  return state.cursor !== null;
}

/** 1-based, for display only: an opaque keyset cursor cannot tell you how many pages exist. */
export function pageNumberOf(state: PageCursor): number {
  return state.trail.length + (state.cursor === null ? 1 : 2);
}

/**
 * A cursor is bound to the tenant, the operation *and* the filters it was minted for, so a filter
 * change must start the walk again. Keeping it would send the service a cursor from another scope,
 * which is `invalid_cursor` — an error the user caused by using the page normally.
 */
export function withFilter(
  filters: UsageFilters,
  patch: Partial<Pick<UsageFilters, "range" | "keyId" | "model">>,
): UsageFilters {
  return { ...filters, ...patch, cursor: null, trail: [] };
}

// ---------------------------------------------------------------------------
// Service queries
// ---------------------------------------------------------------------------

/** The filters as the service sees them: no cursor, no limit — the whole selected scope. */
export function usageScopeQuery(filters: UsageFilters, now: Date): UsageQuery {
  const query: UsageQuery = {
    from: new Date(now.getTime() - RANGES[filters.range]).toISOString(),
    to: now.toISOString(),
  };
  if (filters.keyId !== null) query.key_id = filters.keyId;
  if (filters.model !== null) query.model = filters.model;
  return query;
}

/** One page of that scope. `cursor` is only ever a cursor the service minted. */
export function usagePageQuery(filters: UsageFilters, now: Date): UsageQuery {
  const query: UsageQuery = { ...usageScopeQuery(filters, now), limit: PAGE_SIZE };
  if (filters.cursor !== null) query.cursor = filters.cursor;
  return query;
}

// ---------------------------------------------------------------------------
// Loading / empty / error state machine
// ---------------------------------------------------------------------------

export type Recovery = "retry" | "restart" | "none";

export type ViewState<T> =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "ready"; value: T }
  | { kind: "error"; code: ErrorCode; message: string; recovery: Recovery };

/** Statuses that mean "the platform, later": a reload is a sensible button. */
const RETRYABLE_STATUSES = [429, 500, 503, 504];

/**
 * What the page can offer the user. `invalid_cursor` is the one code a retry cannot help with and a
 * restart can: the page link is stale, so the walk has to begin again.
 */
export function recoveryFor(code: ErrorCode): Recovery {
  if (code === "invalid_cursor") return "restart";
  const status = ERROR_CODE_HTTP_STATUS[code];
  return status !== null && RETRYABLE_STATUSES.includes(status) ? "retry" : "none";
}

/**
 * A sentence for the codes a console reader can actually provoke. Anything else falls back to the
 * service's own safe message rather than inventing one.
 */
const HINTS: Partial<Record<ErrorCode, string>> = {
  invalid_cursor: "This page link is no longer valid. Start from the first page.",
  invalid_request: "One of the filters in this link is not something we can query.",
  forbidden: "Your role in this organization cannot read this.",
  not_found: "We could not find that in your organization.",
  org_suspended:
    "This organization is suspended, so new work is paused. Usage and balances stay readable.",
  rate_limited: "Too many requests just now. Try again in a moment.",
  dependency_unavailable: "A service this page needs is unavailable. Try again in a moment.",
  internal_error: "Something went wrong on our side. Try again in a moment.",
  deadline_exceeded: "This took too long to load. Try again.",
};

export function explainError(code: ErrorCode, fallback: string): string {
  return HINTS[code] ?? fallback;
}

/** `null` means "the request has not come back yet", which is the only loading state there is. */
export function viewStateOf<T>(
  result: Result<T> | null,
  isEmpty: (value: T) => boolean,
): ViewState<T> {
  if (result === null) return { kind: "loading" };
  if (!result.ok) {
    return {
      kind: "error",
      code: result.error.code,
      message: explainError(result.error.code, result.error.message),
      recovery: recoveryFor(result.error.code),
    };
  }
  return isEmpty(result.value) ? { kind: "empty" } : { kind: "ready", value: result.value };
}

// ---------------------------------------------------------------------------
// Row display
// ---------------------------------------------------------------------------

export type Tone = "neutral" | "positive" | "warning" | "muted";

export type SettlementView = { label: string; detail: string; tone: Tone };

/**
 * What happened to the money for one request. The four settlement states plus "not terminal yet"
 * are five different facts, and a table that renders them the same way is how a customer concludes
 * they were charged for our outage.
 */
export function settlementView(
  row: Pick<UsageRow, "settlement_state" | "job_state" | "max_hold">,
): SettlementView {
  switch (row.settlement_state) {
    case null:
      return {
        label: "Not settled yet",
        detail: `This request is ${row.job_state}. Nothing has been charged, and the hold is a ceiling rather than a price.`,
        tone: "neutral",
      };
    case "settled":
      return {
        label: "Settled",
        detail: "Reported usage was priced and drawn from your promotional balance.",
        tone: "positive",
      };
    case "released_free":
      return {
        label: "No charge",
        detail: "The request ended before it produced billable work, so the hold was released.",
        tone: "muted",
      };
    case "released_platform_absorbed":
      return {
        label: "Platform absorbed",
        detail: "The failure was ours. The hold was released and you were not charged.",
        tone: "muted",
      };
    case "held_unknown":
      return {
        label: "Awaiting reconciliation",
        detail:
          "The engine did not report usage we can trust. The hold stays reserved until it is reconciled; it is not a charge.",
        tone: "warning",
      };
  }
}

export type AmountView = {
  /** What was actually charged. Only a settled row can show anything but zero. */
  charged: string;
  /** The outstanding reservation, when there is one. Never the same column as `charged`. */
  held: string | null;
  final: boolean;
};

/**
 * The one rule this page cannot get wrong: a hold, and the estimated tokens behind it, are not a
 * charge. Only `settled` puts a number in the charged column.
 */
export function amountView(row: Pick<UsageRow, "settlement_state" | "cost" | "max_hold">): AmountView {
  const settled = row.settlement_state === "settled";
  return {
    charged: displayMoney(settled ? row.cost : ZERO_MONEY),
    held: row.max_hold === null ? null : displayMoney(row.max_hold),
    final: settled,
  };
}

export type TokensView = { prompt: string; completion: string; note: string | null };

export function tokensView(
  row: Pick<UsageRow, "prompt_tokens" | "completion_tokens" | "usage_certainty">,
): TokensView {
  if (
    row.usage_certainty === "unknown" ||
    row.prompt_tokens === null ||
    row.completion_tokens === null
  ) {
    return {
      prompt: "—",
      completion: "—",
      note: "Usage was not reported for this request. Nothing is estimated into a charge.",
    };
  }
  return { prompt: num(row.prompt_tokens), completion: num(row.completion_tokens), note: null };
}

/** UTC, to the minute, formatted from the string: a table of instants must not shift by locale. */
export function instantLabel(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

export type UsageRowView = {
  requestId: string;
  when: string;
  model: string;
  keyName: string;
  mode: string;
  outcome: string;
  httpStatus: number;
  settlement: SettlementView;
  amount: AmountView;
  tokens: TokensView;
};

export function usageRowView(row: UsageRow): UsageRowView {
  return {
    requestId: row.request_id,
    when: instantLabel(row.created_at),
    model: row.model,
    keyName: row.key_name,
    mode: row.execution_mode,
    outcome: row.terminal_cause ?? row.job_state,
    httpStatus: row.http_status,
    settlement: settlementView(row),
    amount: amountView(row),
    tokens: tokensView(row),
  };
}

/** The models the page can offer as a filter, with the current selection always present. */
export function modelOptions(rows: readonly UsageRow[], selected: string | null): string[] {
  const models = new Set(rows.map((row) => row.model));
  if (selected !== null) models.add(selected);
  return [...models].sort();
}

// ---------------------------------------------------------------------------
// Summary display
// ---------------------------------------------------------------------------

export type SummaryTile = { label: string; value: string; hint: string };

export function summaryTiles(summary: UsageSummary): SummaryTile[] {
  const tokens = summary.prompt_tokens + summary.completion_tokens;
  return [
    {
      label: "Requests",
      value: num(summary.requests),
      hint: `${num(summary.failed_requests)} returned an error`,
    },
    {
      label: "Charged",
      value: displayMoney(summary.cost),
      hint: "Settled requests only — drawn from promotional credit",
    },
    {
      label: "Awaiting reconciliation",
      value: displayMoney(summary.pending_reconciliation),
      hint: "Held while usage is unknown — not a charge",
    },
    {
      label: "Absorbed by the platform",
      value: num(summary.platform_absorbed_requests),
      hint: "Our failures, at no cost to you",
    },
    {
      label: "Tokens",
      value: num(tokens),
      hint: `${num(summary.prompt_tokens)} in · ${num(summary.completion_tokens)} out`,
    },
  ];
}

export type DayView = {
  day: string;
  requests: number;
  promptTokens: number;
  completionTokens: number;
  total: number;
  cost: string;
};

export function dayViews(days: readonly UsageDay[]): DayView[] {
  return days.map((day) => ({
    day: day.day,
    requests: day.requests,
    promptTokens: day.prompt_tokens,
    completionTokens: day.completion_tokens,
    total: day.prompt_tokens + day.completion_tokens,
    cost: displayMoney(day.cost),
  }));
}
