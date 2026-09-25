/**
 * The real `ConsoleServices` read half (C1).
 *
 * Everything a console page reads goes through here: one trusted `SessionContext`, one named query
 * per read, bounded keyset pages with authenticated cursors, and a tenant taken from the session and
 * from nowhere else. Query clients are injected (`QueryPort`), so this module holds no connection,
 * no credential and no environment read — `./server.ts` is the only place either appears.
 *
 * Writes are C3's and trace content is C2's. Their guards live here anyway — field allowlist, role,
 * suspension, ownership — because those are properties of the boundary rather than of the write, and
 * the operation then reports that its effect is not implemented in this task. A refusal from a guard
 * is the contract's refusal; `internal_error` marks only the unimplemented effect.
 */

import { moneyUnits, parseMoney, tryMoneyFromUnits, ZERO_MONEY, type Money } from "../contracts/money.ts";
import {
  API_KEY_CREATE_FIELDS,
  ADMIN_ENTITLEMENTS_FIELDS,
  ADMIN_GRANT_FIELDS,
  ADMIN_SUSPENSION_FIELDS,
  AUDIT_QUERY_FIELDS,
  CALIBRATION_LABEL_FIELDS,
  DEFAULT_PAGE_LIMIT,
  ENTITLEMENT_LIMIT_NAMES,
  FEEDBACK_INPUT_FIELDS,
  JUDGE_SCORE_KINDS,
  MAX_ENTITLEMENT_LIMIT,
  JOB_STATES,
  MAX_PAGE_LIMIT,
  PAGE_QUERY_FIELDS,
  PLATFORM_ACTOR,
  SETTINGS_UPDATE_FIELDS,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_MODES,
  TRACE_QUERY_FIELDS,
  USAGE_QUERY_FIELDS,
  type AdminOrgSummary,
  type ApiKeySummary,
  type AuditEntry,
  type AuditQuery,
  type ConsentHistoryEntry,
  type ConsoleSettings,
  type EntitlementLimitName,
  type ErrorCode,
  type FeedbackEntry,
  type JudgeRun,
  type JudgeSample,
  type JudgeScore,
  type LedgerEntry,
  type OrgEntitlements,
  type Page,
  type PageQuery,
  type Result,
  type SessionContext,
  type TraceDetail,
  type TraceListItem,
  type TraceQuery,
  type UsageDay,
  type UsageQuery,
  type UsageRow,
  type UsageSummary,
  type WalletBalance,
} from "../contracts/types.ts";
import {
  OPERATOR_ONLY_OPERATIONS,
  OWNER_ONLY_OPERATIONS,
  OWNER_OR_OPERATOR_OPERATIONS,
  SUSPENDED_ALLOWED_OPERATIONS,
  type ConsoleOperation,
  type ConsoleServices,
} from "../contracts/services.ts";
import {
  LEDGER_ENTRY_KINDS_V2,
  parseAmount,
  parseCredit,
  parseUsd,
  unitOfRegime,
  type AccountingRegime,
  type Amount,
  type BalanceV2,
  type Credit,
  type LedgerEntryKindV2,
  type LegacyUsdStatement,
  type ReadOutcome,
} from "../contracts/v2/types.ts";
import { creditBalanceOf } from "./credits.ts";
import { cursorScope, decodeCursor, encodeCursor } from "./cursor.ts";
import {
  buildPlan,
  feedbackCountPredicate,
  scopedPort,
  type Keyset,
  type NamedQueryName,
  type Predicate,
  namedQuery,
  postgrestPort,
  QueryPortError,
  type PostgrestClient,
  type QueryPort,
  type Row,
  type SqlValue,
} from "./query.ts";

export type ConsoleServicesConfig = {
  /** PostgreSQL executor for the durable relations (06). */
  pg: QueryPort;
  /** ClickHouse executor for the trace projection (T3). */
  ch: QueryPort;
  /** HMAC key for cursors. Read from the environment by name in `./server.ts`, never here. */
  cursorSecret: string;
};

/** Server only, at module scope; see the note in `./query.ts`. */
if (typeof window !== "undefined") throw new Error("lib/services/console.ts is server-only");

/**
 * The two UTC forms a row may carry (R59-9): PostgREST renders `timestamptz` with `+00:00`, and the
 * fixtures and the API use `Z`. Fractional seconds up to **microseconds** are kept — they are half of
 * every keyset key, so rounding them would make a cursor resume on a row that no longer exists. Still
 * refused: a driver `Date`, the `::text` form (`2026-09-02 07:12:18+00`), a date without a time, and
 * any non-UTC offset.
 */
const ROW_TIMESTAMP = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

/** A caller's filter value: the same two forms, so a console page can pass either one through. */
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$/;


/** No traffic in the window: every figure zero, which is what an empty aggregate means. */
const ZERO_USAGE_SUMMARY: UsageSummary = {
  requests: 0,
  failed_requests: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  cost: ZERO_MONEY,
  pending_reconciliation: ZERO_MONEY,
  platform_absorbed_requests: 0,
};

function ok<T>(value: T): Result<T> {
  return { ok: true, value };
}

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

/**
 * Narrowing for the resolver below: `tenant()` returns either the tenant or a refusal, and the
 * negative branch has to narrow to the tenant — which needs the guard to name the whole `Result`.
 */
function isFailure<T>(value: { orgId: string; row: Row } | Result<T>): value is Result<T> {
  return !("orgId" in value);
}

/** Unknown input fields are `invalid_request`, mirroring pydantic `extra="forbid"` (08 §2). */
function badInput<T>(input: unknown, allowed: readonly string[]): Result<T> | null {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    return fail<T>("invalid_request", "this operation takes an object");
  }
  for (const [name, value] of Object.entries(input)) {
    if (value !== undefined && !allowed.includes(name)) {
      return fail<T>("invalid_request", `${name} is not a field of this request`);
    }
  }
  return null;
}

function instant(value: string): number {
  return Date.parse(value);
}

/**
 * Filters are validated before they are ever bound: a value outside its vocabulary is a client bug
 * and says so, instead of returning an empty page that reads as "no traffic".
 */
function badFilter(query: UsageQuery & Partial<TraceQuery>): string | null {
  for (const name of ["from", "to"] as const) {
    const value = query[name];
    if (value !== undefined && !(typeof value === "string" && RFC3339.test(value) && !Number.isNaN(instant(value)))) {
      return `${name} must be an RFC 3339 UTC timestamp`;
    }
  }
  for (const name of ["key_id", "model"] as const) {
    const value = query[name];
    if (value !== undefined && !(typeof value === "string" && value.length > 0 && value.length <= 200)) {
      return `${name} must be a non-empty identifier`;
    }
  }
  if (query.from !== undefined && query.to !== undefined && instant(query.from) > instant(query.to)) {
    return "from must not be after to";
  }
  const vocabularies: [string, readonly string[], unknown][] = [
    ["job_state", JOB_STATES, query.job_state],
    ["trace_mode", TRACE_MODES, query.trace_mode],
    ["content", TRACE_CONTENT_AVAILABILITY, query.content],
  ];
  for (const [name, allowed, value] of vocabularies) {
    if (value !== undefined && !allowed.includes(value as string)) {
      return `${name} must be one of ${allowed.join(", ")}`;
    }
  }
  if (query.has_feedback !== undefined && typeof query.has_feedback !== "boolean") {
    return "has_feedback must be a boolean";
  }
  return null;
}

/**
 * r2: an aggregate needs a window. `usageSummary` and `usageDaily` collapse history into one
 * figure, so an unbounded call is a scan whose answer nobody can check and which silently mixes the
 * legacy accounting regime into a pilot total. The list keeps its optional window: a page is
 * bounded by its limit. Checked before the tenant lookup, like every other input refusal.
 */
function missingWindow(query: UsageQuery): string | null {
  for (const name of ["from", "to"] as const) {
    if (query[name] === undefined) return `${name} is required: an aggregate needs a window`;
  }
  return null;
}

function badLimit(limit: unknown): string | null {
  if (limit === undefined) return null;
  if (!Number.isInteger(limit) || (limit as number) < 1 || (limit as number) > MAX_PAGE_LIMIT) {
    return `limit must be an integer between 1 and ${MAX_PAGE_LIMIT}`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Row readers. A DTO is built field by field from named columns: nothing from a
// row is spread into a response, so a widened relation cannot widen a DTO.
// ---------------------------------------------------------------------------

/**
 * A stored value that is not what the DTO says it is gets refused, not coerced. `String(value ?? "")`
 * turned a missing column into an empty string and a `Date` into a locale-ish string that is not the
 * cursor key it has to be; `Number(x)` turned `"abc"` into `NaN`, which serialises as `null`. Both
 * produced a page that looked fine and was wrong. Everything here throws, and the boundary guard turns
 * that into one `internal_error` (R41-style masking is separate, and fails closed on its own).
 */
function cell(row: Row, column: string): unknown {
  if (!Object.hasOwn(row, column)) {
    throw new TypeError(`the query did not return the column ${column}`);
  }
  return row[column];
}

function text(row: Row, column: string): string {
  const value = cell(row, column);
  if (typeof value !== "string") throw new TypeError(`${column} must be a string`);
  return value;
}

/**
 * A timestamp crosses as a full-precision RFC 3339 string, never a driver `Date`: it is half of every
 * cursor key, and a `Date` reaching `String()` would corrupt the keyset the next page resumes from.
 */
function timestamp(row: Row, column: string): string {
  return normaliseTimestamp(text(row, column), column);
}

/**
 * `…+00:00` and `…Z` are the same instant, and the DTO carries one form: seconds, then exactly six
 * fractional digits, then `Z`.
 *
 * The width is fixed rather than preserved because PostgREST trims trailing zeros — `.12+00:00` next to
 * `.123456Z` in one relation — and a keyset compares these strings: mixed widths would order
 * `.12` after `.123456`, and the exported suite requires one comparable width across a list. Padding
 * keeps every digit the row had and makes the rest explicit.
 */
function normaliseTimestamp(value: string, column: string): string {
  const match = ROW_TIMESTAMP.exec(value);
  if (match === null) throw new TypeError(`${column} must be an RFC 3339 UTC timestamp`);
  const [, seconds, fraction] = match;
  return `${seconds}.${(fraction ?? "").padEnd(6, "0")}Z`;
}

function optionalTimestamp(row: Row, column: string): string | null {
  const value = cell(row, column);
  if (value === null) return null;
  return timestamp(row, column);
}

function optionalText(row: Row, column: string): string | null {
  const value = cell(row, column);
  if (value === null) return null;
  if (typeof value !== "string") throw new TypeError(`${column} must be a string or null`);
  return value;
}

/** Digits, optionally signed — nothing `Number()` would also accept: `""`, `true`, `[5]`, `0x10`, `1e2`. */
const INTEGER_TEXT = /^-?\d+$/;

function integer(row: Row, column: string): number {
  const value = cell(row, column);
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new TypeError(`${column} must be an integer`);
    return value;
  }
  // `Number(x)` reads null and "" as 0, true as 1, ["5"] as 5 and "0x10" as 16. A null `http_status`
  // that renders as 200-with-a-zero is the kind of wrong number a page shows without anyone noticing.
  if (typeof value !== "string" || !INTEGER_TEXT.test(value)) {
    throw new TypeError(`${column} must be an integer`);
  }
  return Number(value);
}

function optionalInteger(row: Row, column: string): number | null {
  const value = cell(row, column);
  if (value === null) return null;
  return integer(row, column);
}

function flag(row: Row, column: string): boolean {
  const value = cell(row, column);
  if (typeof value === "boolean") return value;
  if (value === 1 || value === 0) return value === 1;
  if (value === "true" || value === "false") return value === "true";
  throw new TypeError(`${column} must be a boolean`);
}

/**
 * The masking decision, fail-closed (R41/R50): a viewer who is not an operator is told `platform`
 * unless the stored marker says explicitly that no operator was involved. A D1 function that forgets
 * to select `by_operator` therefore hides principals rather than publishing them.
 */
function masked(row: Row, session: SessionContext): boolean {
  if (session.isOperator === true) return false;
  return row.by_operator !== false;
}

/**
 * Money crosses as **text** (`numeric(20, 8)` rendered as a string; R59-9, and D1 emits it that way).
 *
 * A JSON number is accepted only below `SAFE_MONEY_NUMBER`, because `toFixed(8)` on a double does not
 * convert — it *fabricates*: a double holds at most 2^53 units of 1e-8, so from about 2^26 dollars the
 * eighth digit is whatever the binary representation happened to round to, and
 * `123456789012.12345678` comes back as `…12345886`. A stored value that arrives as a number at all
 * is a legacy row; the `org_balance` fallback that used to hand them over is gone (S1-fix B1).
 */
const SAFE_MONEY_NUMBER = Math.pow(2, 26);

function money(row: Row, column: string): Money {
  const value = cell(row, column);
  if (value === null) return ZERO_MONEY;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Math.abs(value) >= SAFE_MONEY_NUMBER) {
      throw new TypeError(`${column} must be a decimal string: a number this large cannot hold eight digits`);
    }
    return parseMoney(value.toFixed(8));
  }
  return parseMoney(value);
}

function optionalMoney(row: Row, column: string): Money | null {
  const value = cell(row, column);
  if (value === null) return null;
  return money(row, column);
}

/**
 * A JSON column, fail-closed. A string that does not parse — a PostgreSQL array literal `{a,b}`, say —
 * is refused rather than read as `null`, because `null` is a *meaning* in `OrgEntitlements`
 * ("platform default") and reading a malformed value as it would invert R24 from "these models" to
 * "the default set".
 */
function json(row: Row, column: string): unknown {
  const value = cell(row, column);
  if (value === null) return null;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    throw new TypeError(`${column} is not valid JSON`);
  }
}

/**
 * The key pair, nulled together for a key that has since been deleted.
 *
 * `usage_events.api_key_id` is `on delete set null`, so D1's `console_usage` LEFT JOIN yields **both**
 * `key_id` and `key_name` as NULL for such a row — that is the truth, and the row must still be
 * counted (dropping it understates a cost total). Contract revision r2 made the pair
 * `string | null`, so the two sentinels this used to project (`""` and `"(deleted key)"`) are gone:
 * one was an identifier no caller could filter for and the other was a name nobody chose. A
 * `key_id` filter value must be identifier-shaped (non-empty), so no filter matches such a row
 * either way, and how the absence is *rendered* is the page's decision rather than this one's.
 *
 * Exactly one of the two null is a shape the view cannot produce, so it stays a malformed row
 * rather than something to paper over.
 */
function deletedKeyOr(row: Row): { key_id: string | null; key_name: string | null } {
  const id = optionalText(row, "key_id");
  const name = optionalText(row, "key_name");
  if (id === null && name === null) return { key_id: null, key_name: null };
  if (id === null || name === null) {
    throw new TypeError("a usage row has one of key_id/key_name null: the key join yields both or neither");
  }
  return { key_id: id, key_name: name };
}

/**
 * Which accounting rules wrote the row (contract r2). D1 spells the column
 * `usage_events.settlement_regime` with the values `legacy`/`pilot`; the console vocabulary is
 * `legacy_usd`/`pilot`, so the mapping is explicit and **fails closed**: a regime nobody recognises
 * is a malformed row, never read as `pilot`, because reading it as `pilot` would apply R13's
 * settlement rules to a row that was never settled by them.
 */
function accountingRegimeOf(row: Row): UsageRow["accounting_regime"] {
  const regime = text(row, "settlement_regime");
  if (regime === "legacy") return "legacy_usd";
  if (regime === "pilot") return "pilot";
  throw new TypeError("settlement_regime must be legacy or pilot");
}

function usageRowOf(row: Row): UsageRow {
  return {
    request_id: text(row, "request_id"),
    created_at: timestamp(row, "created_at"),
    model: text(row, "model"),
    ...deletedKeyOr(row),
    accounting_regime: accountingRegimeOf(row),
    // r2: nullable, and null on a legacy row. `text()` refused the whole page for history that
    // predates the pilot columns (wave-2 audit A07); nothing is filled in for them either.
    execution_mode: optionalText(row, "execution_mode") as UsageRow["execution_mode"],
    job_state: optionalText(row, "job_state") as UsageRow["job_state"],
    terminal_cause: optionalText(row, "terminal_cause") as UsageRow["terminal_cause"],
    http_status: integer(row, "http_status"),
    prompt_tokens: optionalInteger(row, "prompt_tokens"),
    completion_tokens: optionalInteger(row, "completion_tokens"),
    usage_certainty: optionalText(row, "usage_certainty") as UsageRow["usage_certainty"],
    settlement_state: optionalText(row, "settlement_state") as UsageRow["settlement_state"],
    cost: money(row, "cost"),
    max_hold: optionalMoney(row, "max_hold"),
    trace_mode: optionalText(row, "trace_mode") as UsageRow["trace_mode"],
  };
}

/**
 * R41: a customer session is told *that* the platform acted, never *who* at the platform did.
 * `by_operator` is a stored column that never leaves this function.
 */
function ledgerEntryOf(row: Row, session: SessionContext): LedgerEntry {
  const actor = optionalText(row, "actor");
  return {
    id: text(row, "id"),
    created_at: timestamp(row, "created_at"),
    delta: money(row, "delta"),
    kind: text(row, "kind") as LedgerEntry["kind"],
    reason: optionalText(row, "reason"),
    ref: optionalText(row, "ref"),
    actor: actor === null ? null : masked(row, session) ? PLATFORM_ACTOR : actor,
  };
}

export function keyOf(row: Row): ApiKeySummary {
  return {
    id: text(row, "id"),
    name: text(row, "name"),
    prefix: text(row, "prefix"),
    created_at: timestamp(row, "created_at"),
    last_used_at: optionalTimestamp(row, "last_used_at"),
    revoked_at: optionalTimestamp(row, "revoked_at"),
    // r2: `api_keys.trace_mode` is a nullable column D1 added, and null is **off**. `text()` threw
    // on it, which denied the whole key list for a key that predates the column (A07).
    trace_mode: optionalText(row, "trace_mode") as ApiKeySummary["trace_mode"],
  };
}

function traceListItemOf(row: Row): TraceListItem {
  return {
    request_id: text(row, "request_id"),
    created_at: timestamp(row, "created_at"),
    model: text(row, "model"),
    key_id: text(row, "key_id"),
    job_state: text(row, "job_state") as TraceListItem["job_state"],
    http_status: integer(row, "http_status"),
    trace_mode: text(row, "trace_mode") as TraceListItem["trace_mode"],
    content: text(row, "content") as TraceListItem["content"],
    loss_reason: text(row, "loss_reason") as TraceListItem["loss_reason"],
    prompt_tokens: optionalInteger(row, "prompt_tokens"),
    completion_tokens: optionalInteger(row, "completion_tokens"),
    ttft_ms: optionalInteger(row, "ttft_ms"),
    wall_ms: integer(row, "wall_ms"),
    cost: money(row, "cost"),
    feedback_count: integer(row, "feedback_count"),
    score_count: integer(row, "score_count"),
  };
}

function traceDetailOf(row: Row, feedback: FeedbackEntry[]): TraceDetail {
  return {
    ...traceListItemOf(row),
    execution_mode: text(row, "execution_mode") as TraceDetail["execution_mode"],
    terminal_cause: optionalText(row, "terminal_cause") as TraceDetail["terminal_cause"],
    error_code: optionalText(row, "error_code") as TraceDetail["error_code"],
    usage_certainty: text(row, "usage_certainty") as TraceDetail["usage_certainty"],
    settlement_state: optionalText(row, "settlement_state") as TraceDetail["settlement_state"],
    timings: {
      auth_ms: optionalInteger(row, "auth_ms"),
      media_ms: optionalInteger(row, "media_ms"),
      admit_ms: optionalInteger(row, "admit_ms"),
      queue_ms: optionalInteger(row, "queue_ms"),
      ttft_ms: optionalInteger(row, "ttft_ms"),
      wall_ms: integer(row, "wall_ms"),
    },
    versions: {
      gateway_version: text(row, "gateway_version"),
      model_revision: text(row, "model_revision"),
      price_snapshot_version: text(row, "price_snapshot_version"),
      trace_schema_version: integer(row, "trace_schema_version"),
    },
    content_expires_at: optionalTimestamp(row, "content_expires_at"),
    metadata_expires_at: timestamp(row, "metadata_expires_at"),
    feedback,
  };
}

/** R41 for feedback: the entry stays a customer signal; only the operator's identity is withheld. */
function feedbackValue(row: Row): FeedbackEntry["value"] {
  const value = cell(row, "value");
  if (typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new TypeError("a feedback value is a boolean, a number or text");
}

function feedbackEntryOf(row: Row, session: SessionContext): FeedbackEntry {
  const value = feedbackValue(row);
  return {
    id: text(row, "id"),
    request_id: text(row, "request_id"),
    created_at: timestamp(row, "created_at"),
    channel: text(row, "channel") as FeedbackEntry["channel"],
    author_role: text(row, "author_role") as FeedbackEntry["author_role"],
    author_principal: masked(row, session) ? PLATFORM_ACTOR : text(row, "author_principal"),
    name: text(row, "name") as FeedbackEntry["name"],
    value,
    comment: optionalText(row, "comment"),
    calibration_set: flag(row, "calibration_set"),
    rubric_version: optionalInteger(row, "rubric_version"),
  };
}

function consentEntryOf(row: Row, session: SessionContext): ConsentHistoryEntry {
  return {
    changed_at: timestamp(row, "changed_at"),
    evaluation_consent: flag(row, "evaluation_consent"),
    changed_by: masked(row, session) ? PLATFORM_ACTOR : text(row, "changed_by"),
  };
}

/**
 * R24 is a three-state fact — `null` is "the platform default", `[]` is "nothing entitled" — so a
 * stored value that is neither a real array nor null is refused rather than read as `null`: that
 * reading would invert a fail-closed denial into the default set. Limit names come from the closed
 * `ENTITLEMENT_LIMIT_NAMES` set, and anything else is a defect rather than a silently kept control.
 */
function entitlementsOf(row: Row): OrgEntitlements {
  const modelIds = json(row, "model_ids");
  if (modelIds !== null && !Array.isArray(modelIds)) {
    throw new TypeError("model_ids must be a JSON array or null");
  }
  if (Array.isArray(modelIds) && modelIds.some((model) => typeof model !== "string")) {
    throw new TypeError("every entitled model id is a string");
  }
  const limitsValue = json(row, "limits");
  if (limitsValue !== null && (typeof limitsValue !== "object" || Array.isArray(limitsValue))) {
    throw new TypeError("limits must be a JSON object or null");
  }
  const limits: Partial<Record<EntitlementLimitName, number>> = {};
  for (const [name, value] of Object.entries((limitsValue ?? {}) as Record<string, unknown>)) {
    if (value === null || value === undefined) continue;
    if (!(ENTITLEMENT_LIMIT_NAMES as readonly string[]).includes(name)) {
      throw new TypeError(`${name} is not an entitlement limit`);
    }
    if (typeof value !== "number" || !Number.isInteger(value) || value < 0 || value > MAX_ENTITLEMENT_LIMIT) {
      throw new TypeError(`${name} must be an integer between 0 and ${MAX_ENTITLEMENT_LIMIT}`);
    }
    limits[name as EntitlementLimitName] = value;
  }
  return {
    org_id: text(row, "org_id"),
    model_ids: modelIds === null ? null : (modelIds as string[]),
    limits,
    updated_at: optionalTimestamp(row, "entitlements_updated_at"),
    updated_by: optionalText(row, "entitlements_updated_by"),
  };
}

function walletOf(ledgerTotal: Money, reservedTotal: Money): WalletBalance | null {
  const available = tryMoneyFromUnits(moneyUnits(ledgerTotal) - moneyUnits(reservedTotal));
  if (available === null) return null;
  return { ledger_total: ledgerTotal, reserved_total: reservedTotal, available };
}

/**
 * A stored JSON blob is not a DTO. Passing `samples` through verbatim handed a page whatever the
 * projection happened to contain — an `org_id`, a provider batch id, the principal who labelled a
 * sample — so every nested field is read by name, from the vocabularies the contract freezes.
 */
function judgeScoreOf(value: unknown): JudgeScore {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("a judge score is an object");
  }
  const row = value as Row;
  const kind = text(row, "kind");
  if (!(JUDGE_SCORE_KINDS as readonly string[]).includes(kind)) throw new TypeError("unknown judge score kind");
  return {
    name: text(row, "name"),
    kind: kind as JudgeScore["kind"],
    value_num: numericOrNull(row, "value_num"),
    value_bool: cell(row, "value_bool") === null ? null : flag(row, "value_bool"),
    value_label: optionalText(row, "value_label"),
    value_text: optionalText(row, "value_text"),
    rationale: optionalText(row, "rationale"),
    rubric_version: integer(row, "rubric_version"),
    judge_model: text(row, "judge_model"),
    judge_model_version: optionalText(row, "judge_model_version"),
    estimated: flag(row, "estimated"),
  };
}

/** A score may be fractional, so it is not `integer()` — but it is still a number and not a coercion. */
function numericOrNull(row: Row, column: string): number | null {
  const value = cell(row, column);
  if (value === null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  throw new TypeError(`${column} must be a number`);
}

/**
 * r2: exactly the four members `console_judge_runs` builds — `jsonb_build_object('sample_id',
 * 'rubric_version', 'request_id', 'scores')`. The previous shape read `id`, `run_id`,
 * `limited_evaluation` and `limited_reason`, none of which that view emits: `id`/`run_id` were
 * `text()` reads of absent members (so every real run would have thrown) and the two limited-*
 * members were fields only a fake could fill. The run carries `limited_evaluation_count`, which is
 * what the console renders.
 *
 * `scores` may be SQL NULL (`judge_samples.scores` is nullable: nothing collected yet), which is
 * an empty list of scores and not a missing field.
 */
function judgeSampleOf(value: unknown): JudgeSample {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("a judge sample is an object");
  }
  const row = value as Row;
  const scores = cell(row, "scores");
  if (scores !== null && !Array.isArray(scores)) throw new TypeError("a judge sample's scores are a list");
  return {
    sample_id: text(row, "sample_id"),
    // The *sample's* rubric version: late results deduplicate by (run, sample, rubric version),
    // so one run may carry samples from more than one.
    rubric_version: integer(row, "rubric_version"),
    // Null: the trace this sample scored has been deleted. An invented id would name another one.
    request_id: optionalText(row, "request_id"),
    scores: (scores ?? []).map(judgeScoreOf),
  };
}

function judgeRunOf(row: Row): JudgeRun {
  const samples = json(row, "samples");
  if (samples !== null && !Array.isArray(samples)) throw new TypeError("samples must be a JSON array");
  return {
    id: text(row, "id"),
    created_at: timestamp(row, "created_at"),
    state: text(row, "state") as JudgeRun["state"],
    mode: text(row, "mode") as JudgeRun["mode"],
    rubric_version: integer(row, "rubric_version"),
    judge_model: text(row, "judge_model"),
    // r2: `judge_runs.judge_model_version` is nullable, and a dry run reaches no provider.
    judge_model_version: optionalText(row, "judge_model_version"),
    sample_count: integer(row, "sample_count"),
    limited_evaluation_count: integer(row, "limited_evaluation_count"),
    budget_reserved: money(row, "budget_reserved"),
    budget_settled: optionalMoney(row, "budget_settled"),
    // r2: the view reaches the consent row through an outer join, so a revoked one is null. The
    // run is still a fact; a snapshot instant is not invented for it.
    consent_snapshot_at: optionalTimestamp(row, "consent_snapshot_at"),
    external_batch_id: optionalText(row, "external_batch_id"),
    quarantine_reason: optionalText(row, "quarantine_reason"),
    samples: (samples ?? []).map(judgeSampleOf),
  };
}

function auditEntryOf(row: Row): AuditEntry {
  const before = json(row, "before");
  const after = json(row, "after");
  return {
    id: text(row, "id"),
    at: timestamp(row, "at"),
    actor_principal: text(row, "actor_principal"),
    action: text(row, "action") as AuditEntry["action"],
    // r2: `audit_entries.target_org_id` is `on delete set null`, so an entry whose target
    // organization is gone keeps the record of the write with no target.
    target_org_id: optionalText(row, "target_org_id"),
    reason: text(row, "reason"),
    before: before === null ? null : (before as Record<string, unknown>),
    after: (after ?? {}) as Record<string, unknown>,
    idempotency_key: text(row, "idempotency_key"),
  };
}

/**
 * One bounded keyset page. `limit + 1` rows are asked for so "is there more" needs no count, the
 * cursor is verified against this exact scope before it is bound, and the next cursor is minted
 * from the last row's sort key.
 */
async function keysetPage<T>(
  port: QueryPort,
  cursorSecret: string,
  name: NamedQueryName,
  scope: string,
  query: PageQuery,
  filters: Record<string, SqlValue>,
  orgId: string | null,
  project: (row: Row) => T,
  keyOf: (row: Row) => Keyset,
  extra: Predicate[] = [],
): Promise<Result<Page<T>>> {
  const limitProblem = badLimit(query.limit);
  if (limitProblem !== null) return fail("invalid_request", limitProblem);
  const limit = query.limit ?? DEFAULT_PAGE_LIMIT;
  let keyset: Keyset | null = null;
  if (query.cursor !== undefined && query.cursor !== null && query.cursor !== "") {
    keyset = decodeCursor(cursorSecret, scope, query.cursor);
    if (keyset === null) return fail("invalid_cursor", "the cursor was not issued by this service for this query");
  }
  const plan = buildPlan(name, { filters, keyset, limit: limit + 1, orgId });
  for (const predicate of extra) plan.predicates.push(predicate);
  const found = await port.run(plan);
  const items = found.slice(0, limit).map(project);
  const more = found.length > limit;
  const lastRow = found[items.length - 1];
  return ok({
    items,
    next_cursor: more && lastRow !== undefined ? encodeCursor(cursorSecret, scope, keyOf(lastRow)) : null,
  });
}

// ---------------------------------------------------------------------------
// The services
// ---------------------------------------------------------------------------

export function createConsoleServices(config: ConsoleServicesConfig): ConsoleServices {
  const { cursorSecret } = config;
  // Every injected port goes through the shared tenant check; nothing here calls one directly.
  const pg = scopedPort(config.pg);
  const ch = scopedPort(config.ch);
  if (typeof cursorSecret !== "string" || cursorSecret.length < 16) {
    throw new Error("createConsoleServices needs a cursor signing secret of at least 16 characters");
  }
  const portFor = (name: NamedQueryName): QueryPort => (name === "traces_page" || name === "trace_by_request" ? ch : pg);

  async function rows(
    name: NamedQueryName,
    options: { filters?: Record<string, SqlValue>; keyset?: Keyset | null; limit?: number | null; orgId?: string | null },
  ): Promise<Row[]> {
    return portFor(name).run(buildPlan(name, options));
  }

  /**
   * The tenant. It comes from the session, and the organization row is what says whether the
   * operation is allowed while suspended (R33): every read stays available and so does
   * `keys.revoke`, because a leaked key must be revocable whatever the organization's status.
   */
  async function tenant<T>(
    session: SessionContext,
    operation: ConsoleOperation,
  ): Promise<{ orgId: string; row: Row } | Result<T>> {
    const found = await rows("org_status", { orgId: session.orgId, limit: 1 });
    const row = found[0];
    if (row === undefined) return fail<T>("not_found", "no such organization");
    if (flag(row, "suspended") && !(SUSPENDED_ALLOWED_OPERATIONS as readonly string[]).includes(operation)) {
      return fail<T>("org_suspended", "this organization is suspended");
    }
    return { orgId: session.orgId, row };
  }

  function requireRole<T>(session: SessionContext, operation: ConsoleOperation): Result<T> | null {
    // `=== true`: a truthy `"false"`, `1` or `{}` arriving from a mis-wired caller is not authority.
    if ((OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation) && session.isOperator !== true) {
      return fail<T>("forbidden", "this operation requires platform-operator authority");
    }
    // Operator authority is the flag; it never stands in for the organization role.
    if ((OWNER_ONLY_OPERATIONS as readonly string[]).includes(operation) && session.role !== "owner") {
      return fail<T>("forbidden", "only an organization owner can change this");
    }
    if (
      (OWNER_OR_OPERATOR_OPERATIONS as readonly string[]).includes(operation) &&
      session.role !== "owner" &&
      session.isOperator !== true
    ) {
      return fail<T>("forbidden", "only an organization owner or a platform operator can read this");
    }
    return null;
  }

  /**
   * One bounded keyset page. `limit + 1` rows are asked for so "is there more" needs no count, the
   * cursor is verified against this exact scope before it is bound, and the next cursor is minted
   * from the last row's sort key.
   */
  async function page<T>(
    name: NamedQueryName,
    scope: string,
    query: PageQuery,
    filters: Record<string, SqlValue>,
    orgId: string | null,
    project: (row: Row) => T,
    keyOf: (row: Row) => Keyset,
    extra: Predicate[] = [],
  ): Promise<Result<Page<T>>> {
    return keysetPage(portFor(name), cursorSecret, name, scope, query, filters, orgId, project, keyOf, extra);
  }

  /**
   * The cursor key. The timestamp is normalised exactly as the DTO's is, so a relation that renders
   * `+00:00` still walks: a keyset compares strings, and mixing the two forms inside one list would
   * order `+` before `Z` and drop rows at the boundary.
   */
  const timeKey = (atColumn: string, idColumn: string) => (row: Row): Keyset => ({
    at: timestamp(row, atColumn),
    id: text(row, idColumn),
  });

  /** Filters, as the named query names them: only these reach a plan. */
  function usageFilters(query: UsageQuery): Record<string, SqlValue> {
    const filters: Record<string, SqlValue> = {};
    if (query.from !== undefined) filters.from = query.from;
    if (query.to !== undefined) filters.to = query.to;
    if (query.key_id !== undefined) filters.key_id = query.key_id;
    if (query.model !== undefined) filters.model = query.model;
    return filters;
  }

  function traceFilters(query: TraceQuery): Record<string, SqlValue> {
    const filters = usageFilters(query);
    if (query.job_state !== undefined) filters.job_state = query.job_state;
    if (query.trace_mode !== undefined) filters.trace_mode = query.trace_mode;
    if (query.content !== undefined) filters.content = query.content;
    return filters;
  }

  /** Ownership before anything else a request id can reach: the tenant is bound, so a foreign id is absent. */
  async function ownedTrace(orgId: string, requestId: string): Promise<Row | null> {
    if (typeof requestId !== "string" || requestId === "") return null;
    const found = await rows("trace_by_request", { orgId, filters: { request_id: requestId }, limit: 1 });
    return found[0] ?? null;
  }

  async function ownedKey(orgId: string, keyId: string): Promise<Row | null> {
    if (typeof keyId !== "string" || keyId === "") return null;
    const found = await rows("key_by_id", { orgId, filters: { key_id: keyId }, limit: 1 });
    return found[0] ?? null;
  }

  async function feedbackFor(orgId: string, requestId: string, session: SessionContext): Promise<FeedbackEntry[]> {
    const found = await rows("feedback_by_request", { orgId, filters: { request_id: requestId } });
    return found.map((row) => feedbackEntryOf(row, session));
  }

  /**
   * The effect is C3's (writes) or C2's (trace content body). The guards above each call still run,
   * so a refusal a customer can provoke is the contract's refusal and not this placeholder.
   */
  function notImplemented<T>(what: string, owner: string): Result<T> {
    return fail<T>("internal_error", `${what} is not implemented in this service yet (${owner})`);
  }

  async function walletFor(orgId: string): Promise<WalletBalance | null> {
    const found = await rows("wallet_summary", { orgId, limit: 1 });
    const row = found[0];
    // A new organization has no wallet row yet: zero total, zero reserved, nothing available.
    if (row === undefined) return walletOf(ZERO_MONEY, ZERO_MONEY);
    return walletOf(money(row, "ledger_total"), money(row, "reserved_total"));
  }

  const services: ConsoleServices = {
    async usage(session, query) {
      const rejected = badInput<Page<UsageRow>>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<Page<UsageRow>>("invalid_request", invalid);
      const resolved = await tenant<Page<UsageRow>>(session, "usage");
      if (isFailure(resolved)) return resolved;
      return page<UsageRow>(
        "usage_page",
        cursorScope(resolved.orgId, "usage", query),
        query,
        usageFilters(query),
        resolved.orgId,
        usageRowOf,
        timeKey("created_at", "request_id"),
      );
    },

    async usageSummary(session, query) {
      const rejected = badInput<UsageSummary>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query) ?? missingWindow(query);
      if (invalid !== null) return fail<UsageSummary>("invalid_request", invalid);
      const resolved = await tenant<UsageSummary>(session, "usageSummary");
      if (isFailure(resolved)) return resolved;
      const found = await rows("usage_summary", { orgId: resolved.orgId, filters: usageFilters(query) });
      /**
       * No rows is no traffic, not a failure.
       *
       * The statement groups by the tenant (so the row it returns says whose totals these are), and a
       * grouped aggregate over zero matching rows returns **zero rows** — in PostgreSQL and in the
       * in-memory double alike. Reading `found[0] ?? {}` therefore turned every empty window into an
       * `internal_error`: a filter that matches nothing, and every newly signed-up organization opening
       * its usage page. Zero rows is safe to answer as zeros: there is nothing for `scopedPort` to check
       * because nothing came back, and a port that had returned a foreign row would have been refused
       * before this line. D1's `console_usage_summary` answers an empty window with one zero row
       * labelled with the guarded organization instead, so both shapes are accepted.
       */
      if (found.length === 0) return ok(ZERO_USAGE_SUMMARY);
      // One tenant, one grouped row. More than one means the grouping or the scoping is not what this
      // service thinks it is, and the totals cannot be attributed.
      if (found.length > 1) {
        return fail<UsageSummary>("internal_error", "a usage summary returned more than one row");
      }
      const row = found[0];
      // A total outside the money domain is caught by the boundary guard below, which turns it
      // into a `Result` error: a read never throws, whatever the rows add up to (N8).
      const cost = money(row, "cost");
      const pending = money(row, "pending_reconciliation");
      return ok({
        requests: integer(row, "requests"),
        failed_requests: integer(row, "failed_requests"),
        prompt_tokens: integer(row, "prompt_tokens"),
        completion_tokens: integer(row, "completion_tokens"),
        cost,
        pending_reconciliation: pending,
        platform_absorbed_requests: integer(row, "platform_absorbed_requests"),
      });
    },

    async usageDaily(session, query) {
      const rejected = badInput<UsageDay[]>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query) ?? missingWindow(query);
      if (invalid !== null) return fail<UsageDay[]>("invalid_request", invalid);
      const resolved = await tenant<UsageDay[]>(session, "usageDaily");
      if (isFailure(resolved)) return resolved;
      const found = await rows("usage_daily", { orgId: resolved.orgId, filters: usageFilters(query) });
      // The cap is the named query's (`hardLimit`), applied here as well: `UsageDay[]` has no cursor,
      // so a longer history is truncated rather than paged, and an executor that ignored the LIMIT
      // must not turn that into an unbounded response.
      const capped = found.slice(0, namedQuery("usage_daily").hardLimit ?? found.length);
      return ok(
        capped.map((row) => ({
          day: text(row, "day"),
          requests: integer(row, "requests"),
          prompt_tokens: integer(row, "prompt_tokens"),
          completion_tokens: integer(row, "completion_tokens"),
          cost: money(row, "cost"),
        })),
      );
    },

    async balances(session) {
      const resolved = await tenant<WalletBalance>(session, "balances");
      if (isFailure(resolved)) return resolved;
      const wallet = await walletFor(resolved.orgId);
      if (wallet === null) return fail<WalletBalance>("internal_error", "this wallet does not fit the money domain");
      return ok(wallet);
    },

    async ledger(session, query) {
      const rejected = badInput<Page<LedgerEntry>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const resolved = await tenant<Page<LedgerEntry>>(session, "ledger");
      if (isFailure(resolved)) return resolved;
      return page<LedgerEntry>(
        "ledger_page",
        cursorScope(resolved.orgId, "ledger", query),
        query,
        {},
        resolved.orgId,
        (row) => ledgerEntryOf(row, session),
        timeKey("created_at", "id"),
      );
    },

    async traces(session, query) {
      const rejected = badInput<Page<TraceListItem>>(query, TRACE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<Page<TraceListItem>>("invalid_request", invalid);
      const resolved = await tenant<Page<TraceListItem>>(session, "traces");
      if (isFailure(resolved)) return resolved;
      return page<TraceListItem>(
        "traces_page",
        cursorScope(resolved.orgId, "traces", query),
        query,
        traceFilters(query),
        resolved.orgId,
        traceListItemOf,
        timeKey("created_at", "request_id"),
        // `has_feedback` is a boolean over a counter, so it joins the plan as a comparison.
        query.has_feedback === undefined ? [] : [feedbackCountPredicate(query.has_feedback)],
      );
    },

    async traceDetail(session, requestId) {
      const resolved = await tenant<TraceDetail>(session, "traceDetail");
      if (isFailure(resolved)) return resolved;
      const row = await ownedTrace(resolved.orgId, requestId);
      if (row === null) return fail<TraceDetail>("not_found", "no such request for this organization");
      return ok(traceDetailOf(row, await feedbackFor(resolved.orgId, requestId, session)));
    },

    async traceContent(session, requestId) {
      const resolved = await tenant<never>(session, "traceContent");
      if (isFailure(resolved)) return resolved;
      const row = await ownedTrace(resolved.orgId, requestId);
      if (row === null) return fail("not_found", "no such request for this organization");
      return notImplemented("trace content resolution", "C2 owns content access and signed references");
    },

    feedback: {
      async list(session, requestId) {
        const resolved = await tenant<FeedbackEntry[]>(session, "feedback.list");
        if (isFailure(resolved)) return resolved;
        const row = await ownedTrace(resolved.orgId, requestId);
        if (row === null) return fail<FeedbackEntry[]>("not_found", "no such request for this organization");
        return ok(await feedbackFor(resolved.orgId, requestId, session));
      },

      async submit(session, input) {
        const rejected = badInput<FeedbackEntry>(input, FEEDBACK_INPUT_FIELDS);
        if (rejected !== null) return rejected;
        const resolved = await tenant<FeedbackEntry>(session, "feedback.submit");
        if (isFailure(resolved)) return resolved;
        const row = await ownedTrace(resolved.orgId, input.request_id);
        if (row === null) return fail<FeedbackEntry>("not_found", "no such request for this organization");
        return notImplemented("feedback submission", "C3 owns console mutations");
      },
    },

    calibration: {
      async label(session, input) {
        const rejected = badInput<FeedbackEntry>(input, CALIBRATION_LABEL_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireRole<FeedbackEntry>(session, "calibration.label");
        if (denied !== null) return denied;
        return notImplemented("calibration labelling", "C3 owns console mutations");
      },

      async list(session, query) {
        const rejected = badInput<Page<FeedbackEntry>>(query, PAGE_QUERY_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireRole<Page<FeedbackEntry>>(session, "calibration.list");
        if (denied !== null) return denied;
        return notImplemented("the calibration set", "C3 owns the calibration surface");
      },
    },

    settings: {
      async get(session) {
        const resolved = await tenant<ConsoleSettings>(session, "settings.get");
        if (isFailure(resolved)) return resolved;
        const found = await rows("settings_get", { orgId: resolved.orgId, limit: 1 });
        const row = found[0];
        if (row === undefined) return fail<ConsoleSettings>("not_found", "no settings for this organization");
        // The cap lives on the named query (`hardLimit`), because the contract gives this read no
        // cursor: a history past the cap is truncated rather than paged, which is a recorded limit
        // rather than an unbounded scan.
        const history = await rows("consent_history", { orgId: resolved.orgId });
        return ok({
          trace_mode: text(row, "trace_mode") as ConsoleSettings["trace_mode"],
          content_retention_days: integer(row, "content_retention_days"),
          evaluation_consent: flag(row, "evaluation_consent"),
          consent_history: history.map((entry) => consentEntryOf(entry, session)),
        });
      },

      async update(session, update) {
        const rejected = badInput<ConsoleSettings>(update, SETTINGS_UPDATE_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireRole<ConsoleSettings>(session, "settings.update");
        if (denied !== null) return denied;
        const resolved = await tenant<ConsoleSettings>(session, "settings.update");
        if (isFailure(resolved)) return resolved;
        return notImplemented("settings update", "C3 owns console mutations");
      },
    },

    keys: {
      async list(session) {
        const resolved = await tenant<ApiKeySummary[]>(session, "keys.list");
        if (isFailure(resolved)) return resolved;
        const found = await rows("keys_list", { orgId: resolved.orgId });
        return ok(found.map(keyOf));
      },

      async create(session, input) {
        const rejected = badInput<never>(input, API_KEY_CREATE_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireRole<never>(session, "keys.create");
        if (denied !== null) return denied;
        const resolved = await tenant<never>(session, "keys.create");
        if (isFailure(resolved)) return resolved;
        return notImplemented("key creation", "C3 owns console mutations");
      },

      async revoke(session, keyId) {
        const denied = requireRole<ApiKeySummary>(session, "keys.revoke");
        if (denied !== null) return denied;
        const resolved = await tenant<ApiKeySummary>(session, "keys.revoke");
        if (isFailure(resolved)) return resolved;
        const row = await ownedKey(resolved.orgId, keyId);
        if (row === null) return fail<ApiKeySummary>("not_found", "no such key for this organization");
        return notImplemented("key revocation", "C3 owns console mutations");
      },
    },

    async adminOrgs(session, query) {
      const rejected = badInput<Page<AdminOrgSummary>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<Page<AdminOrgSummary>>(session, "adminOrgs");
      if (denied !== null) return denied;
      let overflowed = false;
      const result = await page<AdminOrgSummary>(
        "admin_orgs_page",
        cursorScope("operators", "adminOrgs", query),
        query,
        {},
        null,
        (row) => {
          const wallet = walletOf(money(row, "ledger_total"), money(row, "reserved_total"));
          if (wallet === null) overflowed = true;
          return {
            org_id: text(row, "org_id"),
            name: text(row, "name"),
            owner_email: text(row, "owner_email"),
            created_at: text(row, "created_at"),
            suspended: flag(row, "suspended"),
            suspension_reason: optionalText(row, "suspension_reason"),
            balance: wallet ?? { ledger_total: ZERO_MONEY, reserved_total: ZERO_MONEY, available: ZERO_MONEY },
            requests_30d: integer(row, "requests_30d"),
            entitlements: entitlementsOf(row),
          };
        },
        (row) => ({ at: text(row, "name"), id: text(row, "org_id") }),
      );
      if (overflowed) return fail<Page<AdminOrgSummary>>("internal_error", "a wallet does not fit the money domain");
      return result;
    },

    async adminGrant(session, input) {
      const rejected = badInput<never>(input, ADMIN_GRANT_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<never>(session, "adminGrant");
      if (denied !== null) return denied;
      return notImplemented("operator grants", "C3 owns console mutations");
    },

    async adminSetSuspension(session, input) {
      const rejected = badInput<never>(input, ADMIN_SUSPENSION_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<never>(session, "adminSetSuspension");
      if (denied !== null) return denied;
      return notImplemented("suspension control", "C3 owns console mutations");
    },

    async adminSetEntitlements(session, input) {
      const rejected = badInput<never>(input, ADMIN_ENTITLEMENTS_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<never>(session, "adminSetEntitlements");
      if (denied !== null) return denied;
      return notImplemented("entitlement control", "C3 owns console mutations");
    },

    async adminAudit(session, query: AuditQuery) {
      const rejected = badInput<Page<AuditEntry>>(query, AUDIT_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<Page<AuditEntry>>(session, "adminAudit");
      if (denied !== null) return denied;
      if (query.target_org_id !== undefined && typeof query.target_org_id !== "string") {
        return fail<Page<AuditEntry>>("invalid_request", "target_org_id must be an organization id");
      }
      return page<AuditEntry>(
        "admin_audit_page",
        cursorScope("operators", "adminAudit", query),
        query,
        query.target_org_id === undefined ? {} : { target_org_id: query.target_org_id },
        null,
        auditEntryOf,
        timeKey("at", "id"),
      );
    },

    async judgeRuns(session, query) {
      const rejected = badInput<Page<JudgeRun>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireRole<Page<JudgeRun>>(session, "judgeRuns");
      if (denied !== null) return denied;
      const resolved = await tenant<Page<JudgeRun>>(session, "judgeRuns");
      if (isFailure(resolved)) return resolved;
      return page<JudgeRun>(
        "judge_runs_page",
        cursorScope(resolved.orgId, "judgeRuns", query),
        query,
        {},
        resolved.orgId,
        judgeRunOf,
        timeKey("created_at", "id"),
      );
    },
  };

  return guarded(services);
}

// ---------------------------------------------------------------------------
// C0: the consumer context and read port
// ---------------------------------------------------------------------------
//
// The consumer App's reads, for ONE signed-in individual, through the caller's own JWT: RLS, the
// views' guards and D10's `consumer_*` functions decide what comes back, and every tenant-scoped
// row is checked again by `scopedPort`. Nothing here holds a service key, a customer API key or a
// fixture: a read that cannot answer returns a non-ok `Result`, and the page renders "unavailable".

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The part of a GoTrue user the context reads. `email_confirmed_at` is GoTrue's verification. */
export type AuthUser = { id: string; email?: string | null; email_confirmed_at?: string | null };

/** The individual's own consumer account: their wallet and the personal organization it funds. */
export type ConsumerAccount = {
  userId: string;
  email: string;
  walletId: string;
  orgId: string;
  /** R33: a suspended (or retired) individual keeps every read; new work is refused elsewhere. */
  suspended: boolean;
};

/**
 * Where a request stands. `onboarding` is a verified individual whose grant has not been issued
 * (C3A/A2 own the retry); `unavailable` is any failure to find out - never read as onboarding, which
 * would offer a grant flow to someone who has one, nor as signed out.
 */
export type ConsumerContext =
  | { state: "signed_out" }
  | { state: "unverified"; userId: string; email: string }
  | { state: "onboarding"; userId: string; email: string }
  | { state: "ready"; account: ConsumerAccount }
  | { state: "unavailable" };

type AuthAnswer = {
  data: { user: AuthUser | null } | null;
  error: { name?: string; status?: number } | null;
};

/**
 * `supabase.auth.getUser()`'s answer: the user, `null` when there is no valid session (no cookie,
 * an expired or rejected token), or `"unavailable"` when GoTrue could not be asked. An outage is
 * never "signed out": that would send a signed-in customer to a login page that cannot help.
 */
export function authUserOutcome(answer: AuthAnswer): AuthUser | null | "unavailable" {
  const { error } = answer;
  if (error !== null) {
    if (error.name === "AuthSessionMissingError") return null;
    if (error.status === 401 || error.status === 403) return null;
    return "unavailable";
  }
  return answer.data?.user ?? null;
}

/** Resolve the consumer account server-side, from the verified session user and nothing else. */
export async function resolveConsumerContext(port: QueryPort, user: AuthUser | null): Promise<ConsumerContext> {
  if (user === null) return { state: "signed_out" };
  if (typeof user.id !== "string" || !UUID.test(user.id)) return { state: "unavailable" };
  const email = user.email ?? "";
  if (typeof user.email_confirmed_at !== "string" || user.email_confirmed_at === "") {
    return { state: "unverified", userId: user.id, email };
  }
  try {
    const pg = scopedPort(port);
    const wallets = await pg.run(buildPlan("consumer_wallet", { orgId: user.id, limit: 2 }));
    if (wallets.length === 0) return { state: "onboarding", userId: user.id, email };
    // One consumer wallet per individual is a unique index (0006); two is not an account to guess at.
    if (wallets.length !== 1) return { state: "unavailable" };
    const walletId = text(wallets[0], "wallet_id");
    const orgId = text(wallets[0], "org_id");
    const orgs = await pg.run(buildPlan("org_status", { orgId, limit: 1 }));
    if (orgs.length !== 1) return { state: "unavailable" };
    return { state: "ready", account: { userId: user.id, email, walletId, orgId, suspended: flag(orgs[0], "suspended") } };
  } catch {
    return { state: "unavailable" };
  }
}

/** The `rpc` half of a supabase-js client; the real client satisfies it. */
export type RpcClient = {
  rpc(
    fn: string,
    args: Record<string, SqlValue>,
  ): PromiseLike<{ data: unknown; error: { code?: string | null; message?: string | null } | null }>;
};

/** One consumer CREDIT ledger entry. The unit is CREDIT by construction (the view carries only it). */
export type CreditLedgerEntry = {
  entry_id: string;
  created_at: string;
  kind: LedgerEntryKindV2;
  amount: Credit;
  request_id: string | null;
  reason: string;
};

/**
 * One of the individual's requests (D10 `consumer_jobs`). Money is in the row's OWN unit - CREDIT for
 * a credit job, USD for a legacy one - and is never summed across units or converted. `charged` is
 * `null` until the request is settled: an unsettled or uncertain charge is unknown, not zero.
 * `result` is F2C.b's ReadOutcome from the DB's persisted expiry, never the page's clock.
 */
export type ConsumerRequest = {
  request_id: string;
  created_at: string;
  model: string;
  model_revision: string | null;
  execution_mode: string | null;
  state: string;
  outcome_cause: string | null;
  accounting_regime: AccountingRegime;
  unit: "CREDIT" | "USD";
  hold: Amount | null;
  hold_state: string | null;
  charged: Amount | null;
  settlement_state: string | null;
  usage_certainty: string | null;
  usage: { prompt_tokens: number; completion_tokens: number } | null;
  result: ReadOutcome;
  result_expires_at: string | null;
  settled_at: string | null;
};

export type ConsumerReads = {
  balance(): Promise<Result<BalanceV2>>;
  /** `null`: this personal organization has no legacy USD history. Never merged into `balance`. */
  legacyUsd(): Promise<Result<LegacyUsdStatement | null>>;
  ledger(query: PageQuery): Promise<Result<Page<CreditLedgerEntry>>>;
  requests(query: PageQuery): Promise<Result<Page<ConsumerRequest>>>;
  request(requestId: string): Promise<Result<ConsumerRequest>>;
  /** The owned result body while the persisted expiry allows it. Never log or cache it. */
  result(requestId: string): Promise<Result<string>>;
  keys(): Promise<Result<ApiKeySummary[]>>;
};

/** The D10 refusals a consumer may see, each with fixed text (the DB's names identifiers). */
const RPC_REFUSALS: Partial<Record<ErrorCode, string>> = {
  not_found: "no such request for this account",
  result_pending: "this request has no result yet",
  result_expired: "this result has expired and is no longer available",
  invalid_cursor: "the cursor was not issued by this service for this query",
};

function rpcFailure<T>(error: { code?: string | null; message?: string | null }): Result<T> {
  const prefix = /^([a-z_]+):/.exec(error.message ?? "")?.[1] as ErrorCode | undefined;
  if (error.code === "P0001" && prefix !== undefined && RPC_REFUSALS[prefix] !== undefined) {
    return fail(prefix, RPC_REFUSALS[prefix]);
  }
  if (error.code === "42501") return fail("forbidden", "this account cannot read that");
  return fail("dependency_unavailable", "your account data could not be read right now; try again");
}

function amountIn(row: Row, column: string, unit: "CREDIT" | "USD"): Amount | null {
  const value = cell(row, column);
  if (value === null) return null;
  if (typeof value !== "string") throw new TypeError(`${column} must be a decimal string`);
  return parseAmount(value, unit);
}

function resultOutcome(row: Row, usage: ConsumerRequest["usage"]): ReadOutcome {
  if (optionalTimestamp(row, "settled_at") === null) return "pending";
  if (optionalText(row, "settlement_state") === "held_unknown") return "held_unknown";
  if (text(row, "state") !== "succeeded" || usage === null) return "no_result";
  if (flag(row, "result_available")) return "available";
  return optionalTimestamp(row, "result_expires_at") === null ? "unavailable" : "expired";
}

function consumerRequestOf(row: Row): ConsumerRequest {
  const regime = text(row, "accounting_regime");
  const unit = unitOfRegime(regime);
  if (text(row, "unit") !== unit || (unit !== "CREDIT" && unit !== "USD")) {
    throw new TypeError("a request's unit contradicts its accounting regime");
  }
  const prompt = optionalInteger(row, "prompt_tokens");
  const completion = optionalInteger(row, "completion_tokens");
  if ((prompt === null) !== (completion === null)) throw new TypeError("half a usage report");
  const usage = prompt === null || completion === null ? null : { prompt_tokens: prompt, completion_tokens: completion };
  return {
    request_id: text(row, "request_id"),
    created_at: timestamp(row, "created_at"),
    model: text(row, "requested_model"),
    model_revision: optionalText(row, "model_revision"),
    execution_mode: optionalText(row, "execution_mode"),
    state: text(row, "state"),
    outcome_cause: optionalText(row, "outcome_cause"),
    accounting_regime: regime as AccountingRegime,
    unit,
    hold: amountIn(row, "hold", unit),
    hold_state: optionalText(row, "hold_state"),
    charged: amountIn(row, "charged", unit),
    settlement_state: optionalText(row, "settlement_state"),
    usage_certainty: optionalText(row, "usage_certainty"),
    usage,
    result: resultOutcome(row, usage),
    result_expires_at: optionalTimestamp(row, "result_expires_at"),
    settled_at: optionalTimestamp(row, "settled_at"),
  };
}

function creditLedgerEntryOf(row: Row): CreditLedgerEntry {
  const kind = text(row, "kind");
  if (!(LEDGER_ENTRY_KINDS_V2 as readonly string[]).includes(kind)) throw new TypeError("unknown ledger kind");
  if (text(row, "unit") !== "CREDIT") throw new TypeError("a consumer ledger entry is CREDIT");
  const amount = cell(row, "amount");
  if (typeof amount !== "string") throw new TypeError("amount must be a decimal string");
  return {
    entry_id: text(row, "entry_id"),
    created_at: timestamp(row, "created_at"),
    kind: kind as LedgerEntryKindV2,
    amount: parseCredit(amount),
    request_id: optionalText(row, "request_id"),
    reason: text(row, "reason"),
  };
}

function oneRow(data: unknown): Row | null {
  return Array.isArray(data) && data.length === 1 && typeof data[0] === "object" && data[0] !== null ? (data[0] as Row) : null;
}

/**
 * The reads for one resolved account. `pg` is the PostgREST `QueryPort` (`postgrestPort`), `rpc` the
 * same client's RPC entry point; both carry the individual's own JWT.
 */
export function createConsumerReads(
  config: { pg: QueryPort; rpc: RpcClient; cursorSecret: string },
  account: ConsumerAccount,
): ConsumerReads {
  const pg = scopedPort(config.pg);
  const { rpc, cursorSecret } = config;
  if (typeof cursorSecret !== "string" || cursorSecret.length < 16) {
    throw new Error("createConsumerReads needs a cursor signing secret of at least 16 characters");
  }

  const reads: ConsumerReads = {
    async balance() {
      const { data, error } = await rpc.rpc("console_wallet_summary", { p_user: account.userId });
      if (error !== null) return rpcFailure(error);
      return creditBalanceOf(oneRow(data), account.walletId);
    },

    async legacyUsd() {
      const { data, error } = await rpc.rpc("console_legacy_usd_statement", { p_org: account.orgId });
      if (error !== null) return rpcFailure(error);
      const row = oneRow(data);
      if (row === null) return fail("internal_error", "the legacy statement returned no row");
      if (text(row, "org_id") !== account.orgId || text(row, "unit") !== "USD" || text(row, "accounting_regime") !== "legacy_usd") {
        return fail("internal_error", "the legacy statement is not this organization's USD");
      }
      const entries = integer(row, "entry_count");
      if (entries === 0) return ok(null);
      const balance = cell(row, "balance");
      if (typeof balance !== "string") return fail("internal_error", "the legacy balance is not a decimal string");
      return ok({
        schema_version: 2,
        org_id: account.orgId,
        balance: parseUsd(balance),
        entry_count: entries,
        as_of: timestamp(row, "as_of"),
        rollout_hold: flag(row, "rollout_hold"),
      });
    },

    async ledger(query) {
      const rejected = badInput<Page<CreditLedgerEntry>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      return keysetPage(
        pg,
        cursorSecret,
        "credit_ledger_page",
        cursorScope(account.walletId, "credit_ledger", query),
        query,
        {},
        account.walletId,
        creditLedgerEntryOf,
        (row) => ({ at: timestamp(row, "created_at"), id: text(row, "entry_id") }),
      );
    },

    async requests(query) {
      const rejected = badInput<Page<ConsumerRequest>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const limitProblem = badLimit(query.limit);
      if (limitProblem !== null) return fail("invalid_request", limitProblem);
      const limit = query.limit ?? DEFAULT_PAGE_LIMIT;
      // The RPC caps a page at 100, so the look-ahead row is not available at the cap: a full capped
      // page then carries a cursor (the next page may be empty) rather than being read as the last.
      const ask = Math.min(limit + 1, MAX_PAGE_LIMIT);
      const cursor = query.cursor === undefined || query.cursor === "" ? null : query.cursor;
      // D10's cursor is its own keyset; the tenant is the JWT subject inside the function, so a cursor
      // can only ever move within the caller's own rows, and a malformed one is its `invalid_cursor`.
      const { data, error } = await rpc.rpc("consumer_jobs", { p_after: cursor, p_limit: ask });
      if (error !== null) return rpcFailure(error);
      if (!Array.isArray(data)) return fail("internal_error", "the request list did not return rows");
      const rows = data as Row[];
      const shown = rows.slice(0, limit);
      const more = rows.length > limit || (ask === limit && rows.length === limit);
      const last = shown[shown.length - 1];
      return ok({
        items: shown.map(consumerRequestOf),
        next_cursor: more && last !== undefined ? text(last, "cursor") : null,
      });
    },

    async request(requestId) {
      if (typeof requestId !== "string" || !UUID.test(requestId)) return fail("not_found", RPC_REFUSALS.not_found!);
      const { data, error } = await rpc.rpc("consumer_jobs", { p_after: null, p_limit: 1, p_request_id: requestId });
      if (error !== null) return rpcFailure(error);
      const row = oneRow(data);
      if (row === null) return fail("not_found", RPC_REFUSALS.not_found!);
      return ok(consumerRequestOf(row));
    },

    async result(requestId) {
      if (typeof requestId !== "string" || !UUID.test(requestId)) return fail("not_found", RPC_REFUSALS.not_found!);
      const { data, error } = await rpc.rpc("consumer_job_result", { p_request_id: requestId });
      if (error !== null) return rpcFailure(error);
      if (typeof data !== "string") return fail("internal_error", "a result is text");
      return ok(data);
    },

    async keys() {
      const found = await pg.run(buildPlan("keys_list", { orgId: account.orgId }));
      return ok(found.map(keyOf));
    },
  };

  // A read never throws: a port failure is `dependency_unavailable` (retry), anything else - a row
  // that is not what its DTO says - is `internal_error`. Fixed text either way.
  const out = {} as Record<string, unknown>;
  for (const [name, read] of Object.entries(reads)) {
    out[name] = async (...args: unknown[]) => {
      try {
        return await (read as (...a: unknown[]) => Promise<Result<unknown>>)(...args);
      } catch (error) {
        return error instanceof QueryPortError
          ? fail("dependency_unavailable", "your account data could not be read right now; try again")
          : fail("internal_error", "this read failed unexpectedly");
      }
    };
  }
  return out as ConsumerReads;
}

/** The consumer App's request context: who is asking, and - only for a ready account - their reads. */
export type ConsumerSession = { context: ConsumerContext; reads: ConsumerReads | null };

/** The Supabase server client as C0 uses it: GoTrue's `getUser()` plus PostgREST (the real one fits). */
export type ConsumerClient = PostgrestClient & { auth: { getUser(): PromiseLike<AuthAnswer> } };

/**
 * `consumerSession()` without React's `cache` or the Next cookie client, so it is testable (R48).
 *
 * Everything runs as the individual: the client carries their cookie JWT, so RLS, the views' guards
 * and D10's `consumer_*` functions decide what is visible. GoTrue's `getUser()` revalidates the token,
 * and the account is found by wallet owner (`resolveConsumerContext`). The secret and the client are
 * obtained inside the guard, the secret first: any failure - configuration, auth service, database -
 * is `unavailable`, never onboarding (a second grant flow), signed out, a fixture or a zero.
 */
export async function consumerSessionFrom(
  client: () => Promise<ConsumerClient>,
  cursorSecret: () => string,
): Promise<ConsumerSession> {
  try {
    const secret = cursorSecret();
    const supabase = await client();
    const user = authUserOutcome(await supabase.auth.getUser());
    if (user === "unavailable") return { context: { state: "unavailable" }, reads: null };
    const pg = postgrestPort(supabase);
    const context = await resolveConsumerContext(pg, user);
    if (context.state !== "ready") return { context, reads: null };
    return { context, reads: createConsumerReads({ pg, rpc: supabase, cursorSecret: secret }, context.account) };
  } catch {
    return { context: { state: "unavailable" }, reads: null };
  }
}

/** What the console shell (`app/(console)/layout.tsx`, WR-1) does with a request. */
export type ConsoleShell =
  | { kind: "redirect"; to: string }
  | { kind: "render"; email: string; reads: ConsumerReads | null }
  | { kind: "panel"; state: "unverified" | "onboarding" | "unavailable" };

/**
 * The shell's decision. An operator is not a consumer: operator access never waits on a consumer
 * wallet, so an operator whose consumer state is onboarding gets the page (no reads) and reaches
 * /admin, which checks the role itself. Verification and onboarding redirect only to a route that
 * has shipped (`null` until A2/C3A land it): a redirect to a missing route is a 404, not a flow.
 */
export function consoleShell(
  session: ConsumerSession,
  isOperator: boolean,
  routes: { verifyEmail: string | null; onboarding: string | null },
): ConsoleShell {
  const { context, reads } = session;
  switch (context.state) {
    case "signed_out":
      return { kind: "redirect", to: "/login" };
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

type AnyOperation = (...args: never[]) => Promise<Result<unknown>>;

/**
 * 08 §9: a failure is a `Result`, never an exception — a thrown read escapes the error rendering
 * every console page is built on. One wrapper at the boundary makes that structural instead of a
 * discipline each of the twenty-three operations has to remember, and it covers the whole class:
 * a port that rejects, a stored total outside the money domain, a malformed JSON column. The message
 * is fixed and carries no exception text, URL or storage key (08 §3).
 */
function guarded(services: ConsoleServices): ConsoleServices {
  const wrap = (operation: AnyOperation): AnyOperation =>
    async function guardedOperation(...args: never[]) {
      try {
        return await operation(...args);
      } catch {
        return fail("internal_error", "this console operation failed unexpectedly");
      }
    };
  const out = { ...services } as unknown as Record<string, unknown>;
  for (const [name, value] of Object.entries(out)) {
    if (typeof value === "function") {
      out[name] = wrap(value as AnyOperation);
    } else if (typeof value === "object" && value !== null) {
      const group = { ...(value as Record<string, unknown>) };
      for (const [inner, member] of Object.entries(group)) {
        if (typeof member === "function") group[inner] = wrap(member as AnyOperation);
      }
      out[name] = group;
    }
  }
  return out as unknown as ConsoleServices;
}

/** Pure boundary checks, exported for the track's own tests. */
export const __testables = { badFilter, badInput, badLimit, walletOf };
