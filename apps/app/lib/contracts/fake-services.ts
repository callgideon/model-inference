/**
 * Fixture-backed `ConsoleServices` — the executable specification of the console
 * boundary, not a stub returning canned values.
 *
 * It enforces exactly what C must enforce against the real database: the tenant comes
 * from `SessionContext` only, members cannot mutate, non-operators cannot grant or see
 * other organizations, cross-tenant identifiers are `not_found`, cursors are opaque and
 * validated, grants are idempotent, and feedback provenance is server-set. U and V build
 * against this; C must pass the same conformance suite with the real implementation.
 *
 * Everything is derived deterministically from `fixtures/*.json`: two calls to
 * `createFakeConsoleServices()` produce byte-identical data, and each call gets its own
 * mutable copy.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import orgsFixtureJson from "./fixtures/orgs.json" with { type: "json" };
import traceFixtureJson from "./fixtures/traces.json" with { type: "json" };
import judgeFixtureJson from "./fixtures/judge.json" with { type: "json" };
import {
  moneyFromUnits,
  moneyUnits,
  parseMoney,
  tryMoneyFromUnits,
  tryParseMoneyUnits,
  ZERO_MONEY,
  type Money,
} from "./money.ts";
import {
  ADMIN_ENTITLEMENTS_FIELDS,
  ADMIN_GRANT_FIELDS,
  ADMIN_SUSPENSION_FIELDS,
  API_KEY_CREATE_FIELDS,
  CALIBRATION_LABEL_FIELDS,
  CALIBRATION_LABELS,
  DEFAULT_PAGE_LIMIT,
  ENTITLEMENT_LIMIT_NAMES,
  FEEDBACK_ENTRY_NAMES,
  FEEDBACK_INPUT_FIELDS,
  FEEDBACK_NAMES,
  FEEDBACK_RATING_MAX,
  FEEDBACK_RATING_MIN,
  JOB_STATES,
  MAX_CONTENT_RETENTION_DAYS,
  MAX_ENTITLEMENT_LIMIT,
  MAX_FEEDBACK_TEXT_CHARS,
  MAX_GRANT_REASON_CHARS,
  MAX_IDEMPOTENCY_KEY_CHARS,
  MAX_KEY_NAME_CHARS,
  MAX_PAGE_LIMIT,
  MAX_RUBRIC_VERSION,
  ORG_ROLES,
  PAGE_QUERY_FIELDS,
  SETTINGS_UPDATE_FIELDS,
  TERMINAL_JOB_STATES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_MODES,
  TRACE_QUERY_FIELDS,
  USAGE_QUERY_FIELDS,
  type AdminGrantResult,
  type AdminOrgSummary,
  type ApiKeyCreated,
  type ApiKeySummary,
  type ConsoleSettings,
  type ErrorCode,
  type ExecutionMode,
  type FeedbackEntry,
  type FeedbackName,
  type FeedbackValue,
  type JobState,
  type JudgeRun,
  type JudgeSample,
  type JudgeScore,
  type CalibrationLabelValue,
  type EntitlementLimitName,
  type LedgerEntry,
  type OrgEntitlements,
  type Page,
  type PageQuery,
  type Result,
  type SessionContext,
  type SettlementState,
  type TerminalCause,
  type TraceContentAvailability,
  type TraceContentBody,
  type TraceContentView,
  type TraceDetail,
  type TraceListItem,
  type TraceMode,
  type TraceQuery,
  type UsageCertainty,
  type UsageDay,
  type UsageQuery,
  type UsageRow,
  type UsageSummary,
  type WalletBalance,
} from "./types.ts";
import type { ConsoleOperation, ConsoleServices } from "./services.ts";

// ---------------------------------------------------------------------------
// Fixture shapes (the JSON is data; these declarations are how we read it)
// ---------------------------------------------------------------------------

type GrantFixture = { amount: string; reason: string; created_at: string };

type KeyFixture = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  trace_mode: string;
};

type OrgFixture = {
  org_id: string;
  namespace: number;
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  suspension_reason: string | null;
  usage_rows: number;
  all_free: boolean;
  grants: GrantFixture[];
  adjustment: GrantFixture | null;
  /** A historical `purchase` row: R13 keeps the kind renderable, nothing creates one. */
  legacy_purchase: GrantFixture | null;
  keys: KeyFixture[];
  settings: {
    trace_mode: string;
    content_retention_days: number;
    evaluation_consent: boolean;
    consent_history: { changed_at: string; evaluation_consent: boolean; changed_by: string }[];
  };
};

type SessionFixture = {
  userId: string;
  email: string;
  orgId: string;
  orgName: string;
  role: string;
  isOperator: boolean;
};

const orgsFixture = orgsFixtureJson as unknown as {
  clock: string;
  models: string[];
  rate_units_per_token: { input: number; output: number };
  gateway_version: string;
  price_snapshot_version: string;
  orgs: OrgFixture[];
  sessions: Record<"owner" | "member" | "operator" | "otherOwner" | "suspendedOwner", SessionFixture>;
};

const traceFixture = traceFixtureJson as unknown as {
  availability_cycle: string[];
  content_templates: TraceContentBody[];
  seed_feedback: {
    trace_index: number;
    channel: string;
    author_role: string;
    author_principal: string;
    name: string;
    value: FeedbackValue;
    comment: string | null;
    calibration_set: boolean;
    rubric_version: number | null;
    created_at: string;
  }[];
};

const judgeFixture = judgeFixtureJson as unknown as {
  runs: {
    id: string;
    created_at: string;
    state: string;
    mode: string;
    rubric_version: number;
    judge_model: string;
    judge_model_version: string;
    budget_reserved: string;
    budget_settled: string | null;
    consent_snapshot_at: string;
    external_batch_id: string | null;
    quarantine_reason: string | null;
    samples: {
      id: string;
      trace_index: number;
      limited_evaluation: boolean;
      limited_reason: string | null;
      scores: Omit<JudgeScore, "rubric_version" | "judge_model" | "judge_model_version">[];
    }[];
  }[];
};

/** Fixture values are data, so they are checked against the frozen vocabulary on load. */
function pick<T extends string>(allowed: readonly T[], value: unknown, what: string): T {
  if (typeof value === "string" && (allowed as readonly string[]).includes(value)) return value as T;
  throw new TypeError(`fixture ${what}: ${JSON.stringify(value)} is not one of ${allowed.join("|")}`);
}

// ---------------------------------------------------------------------------
// Deterministic helpers
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

/** 08 §5 `TRACE_METADATA_MONTHS`. */
const TRACE_METADATA_MONTHS = 13;

/** Calendar-month arithmetic in UTC: 13 months is not 396 days. */
function plusMonths(epochMs: number, months: number): string {
  const day = new Date(epochMs).getUTCDate();
  const shifted = new Date(epochMs);
  shifted.setUTCDate(1);
  shifted.setUTCMonth(shifted.getUTCMonth() + months);
  // Clamp a day the target month does not have (31 January + 1 month is 28 or 29 February).
  const lastDay = new Date(Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, 0)).getUTCDate();
  shifted.setUTCDate(Math.min(day, lastDay));
  return shifted.toISOString();
}

/** `fb_` + namespace + per-organization ordinal: unique across organizations and across restarts. */
function feedbackId(namespace: number, ordinal: number): string {
  return `fb_${hex(namespace, 4)}${hex(ordinal, 8)}`;
}

function hex(value: number, length: number): string {
  return (value >>> 0)
    .toString(16)
    .padStart(length, "0")
    .slice(-length);
}

/** A stable RFC 4122-shaped v4 identifier per (namespace, index) — no randomness. */
function deterministicUuid(namespace: number, index: number): string {
  const a = hex(namespace * 1000003 + index * 7919, 8);
  const b = hex(index * 2654435761, 8);
  return `${a}-${b.slice(0, 4)}-4${b.slice(4, 7)}-8${hex(namespace * 31 + index, 3)}-${hex(index * 97 + namespace, 6)}${hex(index * 131, 6)}`;
}

function fnv1a(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * A cursor carries the **sort key** of the last row delivered — `(created_at, id)` for the
 * time-ordered lists, `(name, org_id)` for the operator list — and the next page resumes strictly
 * *after* that key. Three properties fall out, all of which the suite requires of C too:
 *
 * - equal timestamps are unambiguous, because the id breaks the tie inside the key;
 * - a row arriving at the head mid-walk shifts nothing, because there is no offset;
 * - the cursor row may *leave the result set* (a filter stops matching it, a row is deleted) and
 *   the walk still continues, because resuming is a comparison and not a lookup.
 */
type SortKey = [string, string];

function compareKeys(a: SortKey, b: SortKey): number {
  if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
  if (a[1] !== b[1]) return a[1] < b[1] ? -1 : 1;
  return 0;
}

function encodeCursor(key: SortKey, scope: string): string {
  return btoa(JSON.stringify({ a: key[0], b: key[1], k: fnv1a(scope) }));
}

/** Any cursor this service did not mint for this exact query is rejected. */
function decodeCursor(cursor: string, scope: string): SortKey | null {
  try {
    const parsed = JSON.parse(atob(cursor)) as { a?: unknown; b?: unknown; k?: unknown };
    if (typeof parsed.a !== "string" || parsed.a === "") return null;
    if (typeof parsed.b !== "string" || parsed.b === "") return null;
    if (parsed.k !== fnv1a(scope)) return null;
    return [parsed.a, parsed.b];
  } catch {
    return null;
  }
}

function ok<T>(value: T): Result<T> {
  return { ok: true, value };
}

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

/** Newest first for the time-ordered lists; `descending` is false only for the operator list. */
function paginate<T>(
  rows: T[],
  query: PageQuery,
  scope: string,
  keyOf: (row: T) => SortKey,
  descending = true,
): Result<Page<T>> {
  const limit = query.limit ?? DEFAULT_PAGE_LIMIT;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
    return fail("invalid_request", `limit must be an integer between 1 and ${MAX_PAGE_LIMIT}`);
  }
  let start = 0;
  if (query.cursor !== undefined && query.cursor !== null && query.cursor !== "") {
    const after = decodeCursor(query.cursor, scope);
    if (after === null) {
      return fail("invalid_cursor", "the cursor was not issued by this service for this query");
    }
    // The first row strictly after the cursor key, by comparison: the cursor's own row does not
    // have to be here any more.
    const index = rows.findIndex((row) =>
      descending ? compareKeys(keyOf(row), after) < 0 : compareKeys(keyOf(row), after) > 0,
    );
    start = index === -1 ? rows.length : index;
  }
  const items = structuredClone(rows.slice(start, start + limit));
  const last = items[items.length - 1];
  const exhausted = start + items.length >= rows.length || last === undefined;
  return ok({ items, next_cursor: exhausted ? null : encodeCursor(keyOf(last), scope) });
}

/**
 * Timestamps are compared as instants, never as strings: the validator accepts both `…:00Z` and
 * `…:00.000Z`, and `.` sorts before `Z`, so a lexical `from` filter would drop a row stamped at
 * exactly the boundary.
 */
function instant(value: string): number {
  return Date.parse(value);
}

/**
 * Unknown input fields are refused, the way pydantic `extra="forbid"` refuses them on the Python
 * side (08 §2). A caller that smuggles `org_id`, `author_role`, `channel` or a storage key gets a
 * 400 instead of a silent no-op that reads as success. A key present with `undefined` is absent.
 */
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

/**
 * The payload half of an idempotency record: the *meaningful* fields, normalised and ordered, so
 * the same submission replays and a changed one conflicts whatever order the caller sent, and a
 * key reused against another organization or another value is a 409 rather than a second effect.
 */
function canonicalPayload(value: Record<string, unknown>): string {
  return JSON.stringify(
    Object.entries(value)
      .filter(([, field]) => field !== undefined)
      .sort(([a], [b]) => a.localeCompare(b)),
  );
}

/** R3: one named signal per submission, with the value type the name implies. */
function badFeedbackBody(input: { name: FeedbackName; value: FeedbackValue; comment?: string | null }): string | null {
  if (!(FEEDBACK_NAMES as readonly string[]).includes(input.name)) {
    return `name must be one of ${FEEDBACK_NAMES.join(", ")}`;
  }
  if (input.name === "thumb") {
    if (typeof input.value !== "boolean") return "a thumb value is a boolean";
  } else if (input.name === "rating") {
    if (
      typeof input.value !== "number" ||
      !Number.isInteger(input.value) ||
      input.value < FEEDBACK_RATING_MIN ||
      input.value > FEEDBACK_RATING_MAX
    ) {
      return `a rating value is an integer from ${FEEDBACK_RATING_MIN} to ${FEEDBACK_RATING_MAX}`;
    }
  } else {
    if (typeof input.value !== "string" || input.value.trim() === "") {
      return `a ${input.name} value is non-empty text`;
    }
    if (input.value.length > MAX_FEEDBACK_TEXT_CHARS) {
      return `a ${input.name} value must be at most ${MAX_FEEDBACK_TEXT_CHARS} characters`;
    }
  }
  const comment = input.comment;
  if (comment !== undefined && comment !== null) {
    if (typeof comment !== "string" || comment.trim() === "") return "comment must be non-empty text when present";
    if (comment.length > MAX_FEEDBACK_TEXT_CHARS) {
      return `comment must be at most ${MAX_FEEDBACK_TEXT_CHARS} characters`;
    }
  }
  return null;
}

function badIdempotencyKey(key: unknown, required: boolean): string | null {
  if (key === undefined || key === null) {
    return required ? "an idempotency key is required" : null;
  }
  if (typeof key !== "string" || key.trim() === "") return "the idempotency key must be a non-empty string";
  if (key.length > MAX_IDEMPOTENCY_KEY_CHARS) {
    return `the idempotency key must be at most ${MAX_IDEMPOTENCY_KEY_CHARS} characters`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Generated organization state
// ---------------------------------------------------------------------------

type Outcome = {
  job_state: JobState;
  terminal_cause: TerminalCause | null;
  http_status: number;
  /** The 08 §3 code for `http_status`; a failure that has no code is not a failure. */
  error_code: ErrorCode | null;
  usage_certainty: UsageCertainty;
  settlement_state: SettlementState;
  billable: boolean;
  holds: boolean;
};

const SUCCEEDED: Outcome = {
  job_state: "succeeded",
  terminal_cause: "completed",
  http_status: 200,
  error_code: null,
  usage_certainty: "authoritative",
  settlement_state: "settled",
  billable: true,
  holds: false,
};

/** Deterministic outcome mix: every settlement state and an outstanding hold appear. */
function outcomeFor(index: number, allFree: boolean): Outcome {
  if (allFree) {
    return index % 2 === 0
      ? {
          job_state: "failed",
          terminal_cause: "platform_error",
          http_status: 503,
          error_code: "dependency_unavailable",
          usage_certainty: "authoritative",
          settlement_state: "released_platform_absorbed",
          billable: false,
          holds: false,
        }
      : {
          job_state: "failed",
          terminal_cause: "invalid_media",
          http_status: 400,
          error_code: "unsupported_media",
          usage_certainty: "authoritative",
          settlement_state: "released_free",
          billable: false,
          holds: false,
        };
  }
  if (index === 0) {
    return {
      job_state: "running",
      terminal_cause: null,
      http_status: 200,
      error_code: null,
      usage_certainty: "unknown",
      settlement_state: "held_unknown",
      billable: false,
      holds: true,
    };
  }
  if (index === 1) {
    return {
      job_state: "queued",
      terminal_cause: null,
      http_status: 200,
      error_code: null,
      usage_certainty: "unknown",
      settlement_state: "held_unknown",
      billable: false,
      holds: true,
    };
  }
  if (index === 2) {
    return {
      job_state: "preparing",
      terminal_cause: null,
      http_status: 200,
      error_code: null,
      usage_certainty: "unknown",
      settlement_state: "held_unknown",
      billable: false,
      holds: true,
    };
  }
  if (index % 37 === 3) {
    return {
      job_state: "failed",
      terminal_cause: "engine_incomplete",
      http_status: 500,
      error_code: "internal_error",
      usage_certainty: "unknown",
      settlement_state: "held_unknown",
      billable: false,
      holds: true,
    };
  }
  if (index % 23 === 4) {
    return {
      job_state: "failed",
      terminal_cause: "platform_error",
      http_status: 503,
      error_code: "dependency_unavailable",
      usage_certainty: "authoritative",
      settlement_state: "released_platform_absorbed",
      billable: false,
      holds: false,
    };
  }
  if (index % 19 === 5) {
    return {
      job_state: "failed",
      terminal_cause: "invalid_media",
      http_status: 400,
      error_code: "unsupported_media",
      usage_certainty: "authoritative",
      settlement_state: "released_free",
      billable: false,
      holds: false,
    };
  }
  if (index % 29 === 6) {
    return {
      job_state: "cancelled",
      terminal_cause: "client_cancelled",
      http_status: 200,
      error_code: null,
      usage_certainty: "authoritative",
      settlement_state: "settled",
      billable: true,
      holds: false,
    };
  }
  if (index % 41 === 7) {
    return {
      job_state: "expired",
      terminal_cause: "queue_wait_expired",
      http_status: 504,
      error_code: "deadline_exceeded",
      usage_certainty: "authoritative",
      settlement_state: "released_free",
      billable: false,
      holds: false,
    };
  }
  return SUCCEEDED;
}

type OrgState = {
  org_id: string;
  /** Fixture namespace, mixed into every generated id so two organizations cannot collide. */
  namespace: number;
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  suspension_reason: string | null;
  entitlements: OrgEntitlements;
  settings: ConsoleSettings;
  keys: ApiKeySummary[];
  usage: UsageRow[];
  ledger: LedgerEntry[];
  traces: TraceDetail[];
  content: Map<string, TraceContentView>;
  judge: JudgeRun[];
  counter: number;
  /**
   * Feedback ids are minted per organization and never restart: the namespace keeps two
   * organizations apart and the counter starts past the seeded rows, so no submission can
   * collide with a seeded id however many are added.
   */
  feedbackCounter: number;
};

/**
 * N1: rows that share a timestamp, placed either side of the page boundaries the suite walks (7
 * and 100). A cursor that carried only `created_at` would either repeat or skip one of these; the
 * `(created_at, id)` key does neither, and the suite asserts a tie is actually present so the case
 * cannot quietly disappear from the fixture.
 */
const TIE_INDICES = new Set([6, 7, 13, 14, 99, 100, 106, 107]);

const CLOCK_MS = Date.parse(orgsFixture.clock);
const MAX_OUTPUT_TOKENS = 2048;
const INPUT_UNITS = BigInt(orgsFixture.rate_units_per_token.input);
const OUTPUT_UNITS = BigInt(orgsFixture.rate_units_per_token.output);

function costOf(promptTokens: number, completionTokens: number): Money {
  return moneyFromUnits(BigInt(promptTokens) * INPUT_UNITS + BigInt(completionTokens) * OUTPUT_UNITS);
}

const EXECUTION_MODE_CYCLE: ExecutionMode[] = ["sync", "stream", "async"];

/**
 * Capture mode decides what can exist; full-mode rows then cycle through the remaining
 * states (`sequence` counts full-mode rows only, so every state is reached).
 */
function availabilityFor(mode: TraceMode, sequence: number, agedOut: boolean): TraceContentAvailability {
  if (mode === "off") return "off";
  if (mode === "minimal") return "metadata_only";
  const cycled = pick(
    TRACE_CONTENT_AVAILABILITY,
    traceFixture.availability_cycle[sequence % traceFixture.availability_cycle.length],
    "availability_cycle",
  );
  // Expiry is a fact about the clock, not a slot in a cycle: content is `expired` exactly when
  // `created_at + retention` has passed, so `content_expires_at` never precedes `created_at`.
  if (agedOut) return "expired";
  return cycled === "expired" ? "available" : cycled;
}

/** The sort key of a time-ordered row, and the cursor's payload for these lists. */
function timeKey(row: { created_at: string; id: string }): SortKey {
  return [row.created_at, row.id];
}

function usageKey(row: UsageRow): SortKey {
  return [row.created_at, row.request_id];
}

function traceKey(row: TraceListItem): SortKey {
  return [row.created_at, row.request_id];
}

function buildOrg(spec: OrgFixture): OrgState {
  const random = mulberry32(spec.namespace * 7919 + 13);
  const keys: ApiKeySummary[] = spec.keys.map((key) => ({
    id: key.id,
    name: key.name,
    prefix: key.prefix,
    created_at: key.created_at,
    last_used_at: key.last_used_at,
    revoked_at: key.revoked_at,
    trace_mode: pick(TRACE_MODES, key.trace_mode, `${spec.name} key trace_mode`),
  }));
  const activeKeys = keys.filter((key) => key.revoked_at === null);

  const usage: UsageRow[] = [];
  const traces: TraceDetail[] = [];
  let fullModeRows = 0;
  const content = new Map<string, TraceContentView>();
  const retentionMs = spec.settings.content_retention_days * 86400000;

  for (let i = 0; i < spec.usage_rows; i += 1) {
    const key = activeKeys[i % activeKeys.length];
    const model = orgsFixture.models[i % orgsFixture.models.length];
    // 137 s apart, then 3 days apart past row 119, so the oldest rows are genuinely older than
    // the 30-day retention window and one of them can be `expired` for real. Still monotonic —
    // and deliberately not *strictly* monotonic at TIE_INDICES, where a row repeats its
    // predecessor's timestamp so a page boundary lands inside a group of equal timestamps.
    const tieShift = TIE_INDICES.has(i) ? 1 : 0;
    const createdMs =
      CLOCK_MS - (i + 1 - tieShift) * 137000 - Math.max(0, i - tieShift - 119) * 3 * 86400000;
    const created_at = new Date(createdMs).toISOString();
    const promptTokens = 512 + Math.floor(random() * 20000);
    const completionTokens = 32 + Math.floor(random() * 480);
    const ttft = 180 + Math.floor(random() * 900);
    const wall = ttft + 400 + Math.floor(random() * 4000);
    const outcome = outcomeFor(i, spec.all_free);
    const known = outcome.usage_certainty === "authoritative";
    const request_id = deterministicUuid(spec.namespace, i + 1);
    // R13: nothing is settled until the request is terminal, so the column is null before then.
    const settlement: SettlementState | null = (TERMINAL_JOB_STATES as readonly string[]).includes(
      outcome.job_state,
    )
      ? outcome.settlement_state
      : null;

    usage.push({
      request_id,
      created_at,
      model,
      key_id: key.id,
      key_name: key.name,
      execution_mode: EXECUTION_MODE_CYCLE[i % EXECUTION_MODE_CYCLE.length],
      job_state: outcome.job_state,
      terminal_cause: outcome.terminal_cause,
      http_status: outcome.http_status,
      prompt_tokens: known ? promptTokens : null,
      completion_tokens: known ? completionTokens : null,
      usage_certainty: outcome.usage_certainty,
      settlement_state: settlement,
      cost: outcome.billable ? costOf(promptTokens, completionTokens) : ZERO_MONEY,
      max_hold: outcome.holds ? costOf(promptTokens, MAX_OUTPUT_TOKENS) : null,
      trace_mode: key.trace_mode,
    });

    const availability = availabilityFor(key.trace_mode, fullModeRows, createdMs + retentionMs < CLOCK_MS);
    if (key.trace_mode === "full") fullModeRows += 1;
    const list: TraceListItem = {
      request_id,
      created_at,
      model,
      key_id: key.id,
      job_state: outcome.job_state,
      http_status: outcome.http_status,
      trace_mode: key.trace_mode,
      content: availability,
      loss_reason: availability === "lost" ? "memory_budget" : "none",
      prompt_tokens: known ? promptTokens : null,
      completion_tokens: known ? completionTokens : null,
      ttft_ms: outcome.job_state === "succeeded" ? ttft : null,
      wall_ms: wall,
      cost: outcome.billable ? costOf(promptTokens, completionTokens) : ZERO_MONEY,
      feedback_count: 0,
      score_count: 0,
    };
    const contentExpiresAt =
      availability === "off" || availability === "metadata_only"
        ? null
        : new Date(createdMs + retentionMs).toISOString();

    traces.push({
      ...list,
      execution_mode: usage[i].execution_mode,
      terminal_cause: outcome.terminal_cause,
      error_code: outcome.error_code,
      usage_certainty: outcome.usage_certainty,
      settlement_state: settlement,
      timings: {
        auth_ms: 2,
        media_ms: outcome.terminal_cause === "invalid_media" ? null : 120 + Math.floor(random() * 300),
        admit_ms: 8,
        queue_ms: 40 + Math.floor(random() * 900),
        ttft_ms: list.ttft_ms,
        wall_ms: wall,
      },
      versions: {
        gateway_version: orgsFixture.gateway_version,
        model_revision: model,
        price_snapshot_version: orgsFixture.price_snapshot_version,
        trace_schema_version: 1,
      },
      content_expires_at: contentExpiresAt,
      metadata_expires_at: plusMonths(createdMs, TRACE_METADATA_MONTHS),
      feedback: [],
    });

    const template = traceFixture.content_templates[i % traceFixture.content_templates.length];
    content.set(request_id, {
      request_id,
      availability,
      expires_at: contentExpiresAt,
      content:
        availability === "available"
          ? { ...structuredClone(template), request: { ...structuredClone(template.request), model } }
          : null,
    });
  }

  const ledger: LedgerEntry[] = spec.grants.map((grant, index) => ({
    id: deterministicUuid(spec.namespace + 500, index + 1),
    created_at: grant.created_at,
    delta: parseMoney(grant.amount),
    kind: "grant",
    reason: grant.reason,
    ref: null,
    actor: orgsFixture.sessions.operator.email,
  }));
  usage.forEach((row, index) => {
    if (row.settlement_state !== "settled") return;
    ledger.push({
      id: deterministicUuid(spec.namespace + 700, index + 1),
      created_at: row.created_at,
      delta: moneyFromUnits(-moneyUnits(row.cost)),
      kind: "usage",
      reason: null,
      ref: row.request_id,
      actor: null,
    });
  });
  if (spec.adjustment !== null) {
    ledger.push({
      id: deterministicUuid(spec.namespace + 900, 1),
      created_at: spec.adjustment.created_at,
      delta: parseMoney(spec.adjustment.amount),
      kind: "adjustment",
      reason: spec.adjustment.reason,
      ref: null,
      actor: orgsFixture.sessions.operator.email,
    });
  }
  // R13: a migrated `purchase` row. It has to render; no operation here creates one.
  if (spec.legacy_purchase !== null) {
    ledger.push({
      id: deterministicUuid(spec.namespace + 1100, 1),
      created_at: spec.legacy_purchase.created_at,
      delta: parseMoney(spec.legacy_purchase.amount),
      kind: "purchase",
      reason: spec.legacy_purchase.reason,
      ref: null,
      actor: null,
    });
  }
  // Every time-ordered list is sorted by exactly the key the cursor carries, newest first, with the
  // id breaking a timestamp tie. Without the tiebreak the order is not total and a keyset walk
  // across a group of equal timestamps is undefined.
  ledger.sort((a, b) => -compareKeys(timeKey(a), timeKey(b)));
  usage.sort((a, b) => -compareKeys(usageKey(a), usageKey(b)));
  traces.sort((a, b) => -compareKeys(traceKey(a), traceKey(b)));

  const org: OrgState = {
    org_id: spec.org_id,
    namespace: spec.namespace,
    name: spec.name,
    owner_email: spec.owner_email,
    created_at: spec.created_at,
    suspended: spec.suspended,
    suspension_reason: spec.suspension_reason,
    // No entitlement record until an operator writes one: an empty model list means the platform
    // default set, which is what an organization that has never been configured gets.
    entitlements: { org_id: spec.org_id, model_ids: [], limits: {}, updated_at: null, updated_by: null },
    settings: {
      trace_mode: pick(TRACE_MODES, spec.settings.trace_mode, `${spec.name} settings trace_mode`),
      content_retention_days: spec.settings.content_retention_days,
      evaluation_consent: spec.settings.evaluation_consent,
      consent_history: spec.settings.consent_history.map((entry) => ({ ...entry })),
    },
    keys,
    usage,
    ledger,
    traces,
    content,
    judge: [],
    counter: 0,
    feedbackCounter: 0,
  };

  // Seeded feedback and judge runs belong to the established organization only.
  if (spec.grants.length > 0) {
    traceFixture.seed_feedback.forEach((seed) => {
      const trace = org.traces[seed.trace_index];
      if (trace === undefined) return;
      org.feedbackCounter += 1;
      trace.feedback.push({
        id: feedbackId(spec.namespace, org.feedbackCounter),
        request_id: trace.request_id,
        created_at: seed.created_at,
        channel: seed.channel === "api" ? "api" : "console",
        author_role: seed.author_role === "operator" ? "operator" : seed.author_role === "judge" ? "judge" : "customer",
        author_principal: seed.author_principal,
        name: pick(FEEDBACK_ENTRY_NAMES, seed.name, "seed_feedback name"),
        value: seed.value,
        comment: seed.comment,
        calibration_set: seed.calibration_set,
        rubric_version: seed.rubric_version,
      });
      trace.feedback_count = trace.feedback.length;
    });

    org.judge = judgeFixture.runs.map((run) => {
      const samples: JudgeSample[] = run.samples.map((sample) => {
        const trace = org.traces[sample.trace_index];
        return {
          id: sample.id,
          run_id: run.id,
          request_id: trace === undefined ? deterministicUuid(spec.namespace, 1) : trace.request_id,
          limited_evaluation: sample.limited_evaluation,
          limited_reason:
            sample.limited_reason === "no_media"
              ? "no_media"
              : sample.limited_reason === "content_expired"
                ? "content_expired"
                : null,
          scores: sample.scores.map((score) => ({
            ...score,
            rubric_version: run.rubric_version,
            judge_model: run.judge_model,
            judge_model_version: run.judge_model_version,
          })),
        };
      });
      for (const sample of samples) {
        const trace = org.traces.find((candidate) => candidate.request_id === sample.request_id);
        if (trace !== undefined) trace.score_count += sample.scores.length;
      }
      return {
        id: run.id,
        created_at: run.created_at,
        state: run.state as JudgeRun["state"],
        mode: run.mode === "live" ? "live" : "dry_run",
        rubric_version: run.rubric_version,
        judge_model: run.judge_model,
        judge_model_version: run.judge_model_version,
        sample_count: samples.length,
        limited_evaluation_count: samples.filter((sample) => sample.limited_evaluation).length,
        budget_reserved: parseMoney(run.budget_reserved),
        budget_settled: run.budget_settled === null ? null : parseMoney(run.budget_settled),
        consent_snapshot_at: run.consent_snapshot_at,
        external_batch_id: run.external_batch_id,
        quarantine_reason: run.quarantine_reason,
        samples,
      };
    });
    org.judge.sort((a, b) => -compareKeys(timeKey(a), timeKey(b)));
  }

  return org;
}

// ---------------------------------------------------------------------------
// The fake service
// ---------------------------------------------------------------------------

export type FakeSessions = {
  owner: SessionContext;
  member: SessionContext;
  operator: SessionContext;
  otherOwner: SessionContext;
  /** R18: an owner of a suspended organization, so `org_suspended` is reachable. */
  suspendedOwner: SessionContext;
};

export type FakeIds = {
  orgId: string;
  otherOrgId: string;
  /** A request of the session organization whose trace content is `available`. */
  availableRequestId: string;
  /** A request captured with trace mode `off`. */
  offRequestId: string;
  /** A request that belongs to the other organization. */
  otherOrgRequestId: string;
  /** A well-formed identifier that exists nowhere. */
  unknownRequestId: string;
  keyId: string;
  otherOrgKeyId: string;
  /** R18: an organization that is already suspended when the harness is built. */
  suspendedOrgId: string;
};

/**
 * Where an injected failure fires. `before` is an ordinary refusal — nothing has happened yet.
 * `after_write` models the Python `FailurePlan`'s crash-after-commit: the effect *and* its
 * idempotency record are committed (one step, as they must be in one transaction), and then the
 * response is lost. The retry that follows must replay, not repeat.
 */
export type FailurePhase = "before" | "after_write";

export type FakeConsoleServices = ConsoleServices & {
  sessions: FakeSessions;
  ids: FakeIds;
  /** Deterministic failure injection: the next call of `operation` returns this error. */
  failNext(operation: ConsoleOperation, code: ErrorCode, message?: string, phase?: FailurePhase): void;
  /**
   * Fake-only escape hatch for one assertion the contract cannot make from the outside: that a
   * created key's secret is retained nowhere. It returns the whole internal state, so a test can
   * deep-scan it. Nothing but a test may call it, and C has no equivalent.
   */
  unsafeDebugState(): unknown;
};

function sessionOf(fixture: SessionFixture): SessionContext {
  return {
    userId: fixture.userId,
    email: fixture.email,
    orgId: fixture.orgId,
    orgName: fixture.orgName,
    role: pick(ORG_ROLES, fixture.role, "session role"),
    isOperator: fixture.isOperator,
  };
}

export function createFakeConsoleServices(): FakeConsoleServices {
  const orgs = new Map<string, OrgState>();
  for (const spec of orgsFixture.orgs) orgs.set(spec.org_id, buildOrg(spec));

  const injected = new Map<ConsoleOperation, { code: ErrorCode; message: string; phase: FailurePhase }[]>();
  /**
   * Idempotency records keyed by caller organization + operation + key. The *target* organization
   * and the rest of the payload are part of the stored payload, never of the key: a key replayed
   * against another organization is a changed payload, so it is a 409 and not a second grant
   * (01-contracts, "changed payload is 409"; U3 "duplicate submit creates one audited grant").
   *
   * A record is written only after the operation has completed, and every operation validates
   * before it mutates — so a refused call stores nothing and leaves nothing behind, and the retry
   * that follows it still produces exactly one effect.
   */
  const idempotency = new Map<string, { payload: string; value: unknown }>();
  let clockMs = CLOCK_MS;

  function nextTimestamp(): string {
    clockMs += 1000;
    return new Date(clockMs).toISOString();
  }

  function recordName(session: SessionContext, operation: ConsoleOperation, key: string): string {
    return `${session.orgId}|${operation}|${key}`;
  }

  /** The stored result, an `idempotency_conflict`, or null when this key is unused. */
  function replayOf<T>(
    session: SessionContext,
    operation: ConsoleOperation,
    key: string | undefined | null,
    payload: string,
  ): Result<T> | null {
    if (key === undefined || key === null) return null;
    const previous = idempotency.get(recordName(session, operation, key));
    if (previous === undefined) return null;
    if (previous.payload !== payload) {
      return fail<T>("idempotency_conflict", "this idempotency key was used with a different payload");
    }
    return ok(structuredClone(previous.value) as T);
  }

  function remember(
    session: SessionContext,
    operation: ConsoleOperation,
    key: string | undefined | null,
    payload: string,
    value: unknown,
  ): void {
    if (key === undefined || key === null) return;
    idempotency.set(recordName(session, operation, key), { payload, value: structuredClone(value) });
  }

  function intercept<T>(operation: ConsoleOperation, phase: FailurePhase = "before"): Result<T> | null {
    const queue = injected.get(operation);
    if (queue === undefined || queue.length === 0) return null;
    if (queue[0].phase !== phase) return null;
    const next = queue.shift();
    if (next === undefined) return null;
    return fail<T>(next.code, next.message);
  }

  /**
   * The write and its idempotency record are one step. A real store must do the same inside one
   * transaction: if they can diverge, a crash between them turns the next retry into a second
   * effect. `after_write` injection fires only once both are committed, which is the failure a
   * client actually sees — a lost response, not a lost write.
   */
  function commit<T>(
    operation: ConsoleOperation,
    session: SessionContext,
    key: string | undefined | null,
    payload: string,
    apply: () => T,
    record: (value: T) => unknown = (value) => value,
  ): Result<T> {
    const value = apply();
    remember(session, operation, key, payload, record(value));
    const lost = intercept<T>(operation, "after_write");
    if (lost !== null) return lost;
    return ok(value);
  }

  /** Tenant resolution: the org comes from the session and nowhere else. */
  function tenant<T>(session: SessionContext): { org: OrgState } | Result<T> {
    const org = orgs.get(session.orgId);
    if (org === undefined) return fail<T>("not_found", "no such organization");
    if (org.suspended) return fail<T>("org_suspended", "this organization is suspended");
    return { org };
  }

  function isError<T>(value: { org: OrgState } | Result<T>): value is Result<T> {
    return !("org" in value);
  }

  function requireOwner<T>(session: SessionContext): Result<T> | null {
    if (session.role !== "owner") {
      return fail<T>("forbidden", "only an organization owner can change this");
    }
    return null;
  }

  function requireOperator<T>(session: SessionContext): Result<T> | null {
    if (!session.isOperator) {
      return fail<T>("forbidden", "this operation requires platform-operator authority");
    }
    return null;
  }

  /** R13: evaluation runs are the owner's and the platform's business, not a member's. */
  function requireOwnerOrOperator<T>(session: SessionContext): Result<T> | null {
    if (session.role !== "owner" && !session.isOperator) {
      return fail<T>("forbidden", "only an organization owner or a platform operator can read this");
    }
    return null;
  }

  /**
   * The wallet as it would stand after `deltaUnits` is added to the ledger — `deltaUnits` of zero
   * is simply the current balance. Null means some component leaves the `numeric(20, 8)` domain,
   * which is the answer a mutating operation needs *before* it writes: `moneyFromUnits` would
   * throw, and a throw after a partial write is how a double grant happens.
   */
  function balanceOf(org: OrgState, deltaUnits = BigInt(0)): WalletBalance | null {
    let ledgerUnits = deltaUnits;
    for (const entry of org.ledger) ledgerUnits += moneyUnits(entry.delta);
    let reservedUnits = BigInt(0);
    for (const row of org.usage) if (row.max_hold !== null) reservedUnits += moneyUnits(row.max_hold);
    const ledger_total = tryMoneyFromUnits(ledgerUnits);
    const reserved_total = tryMoneyFromUnits(reservedUnits);
    const available = tryMoneyFromUnits(ledgerUnits - reservedUnits);
    if (ledger_total === null || reserved_total === null || available === null) return null;
    return { ledger_total, reserved_total, available };
  }

  /**
   * 08 §9 promises "validated filters". A filter value outside its vocabulary is a client bug and
   * must say so, rather than silently returning an empty page that reads as "no traffic".
   */
  function badFilter(query: UsageQuery & Partial<TraceQuery>): string | null {
    for (const name of ["from", "to"] as const) {
      const value = query[name];
      if (
        value !== undefined &&
        !(typeof value === "string" && RFC3339.test(value) && !Number.isNaN(instant(value)))
      ) {
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
      if (value !== undefined && !(typeof value === "string" && allowed.includes(value))) {
        return `${name} must be one of ${allowed.join(", ")}`;
      }
    }
    if (query.has_feedback !== undefined && typeof query.has_feedback !== "boolean") {
      return "has_feedback must be a boolean";
    }
    return null;
  }

  function inRange(createdAt: string, query: { from?: string; to?: string }): boolean {
    const at = instant(createdAt);
    return (
      (query.from === undefined || at >= instant(query.from)) &&
      (query.to === undefined || at <= instant(query.to))
    );
  }

  function usageRowsFor(org: OrgState, query: UsageQuery): UsageRow[] {
    return org.usage.filter(
      (row) =>
        inRange(row.created_at, query) &&
        (query.key_id === undefined || row.key_id === query.key_id) &&
        (query.model === undefined || row.model === query.model),
    );
  }

  function traceRowsFor(org: OrgState, query: TraceQuery): TraceListItem[] {
    return org.traces.filter(
      (row) =>
        // R13: an off-mode request has no trace row at all, so it never appears in this list. Its
        // usage row still links to `traceDetail`, which reports availability `off`.
        row.trace_mode !== "off" &&
        inRange(row.created_at, query) &&
        (query.key_id === undefined || row.key_id === query.key_id) &&
        (query.model === undefined || row.model === query.model) &&
        (query.job_state === undefined || row.job_state === query.job_state) &&
        (query.trace_mode === undefined || row.trace_mode === query.trace_mode) &&
        (query.content === undefined || row.content === query.content) &&
        (query.has_feedback === undefined || query.has_feedback === row.feedback_count > 0),
    );
  }

  /**
   * A cursor is bound to its tenant, its operation and its filters — but not to `limit`, so a
   * caller may change the page size mid-walk exactly as it can against a keyset implementation.
   */
  function scopeOf(org: OrgState, operation: string, query: object): string {
    const parts = Object.entries(query)
      .filter(([name, value]) => name !== "cursor" && name !== "limit" && value !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return `${org.org_id}|${operation}|${JSON.stringify(parts)}`;
  }

  function ownedTrace(org: OrgState, requestId: string): TraceDetail | undefined {
    return org.traces.find((trace) => trace.request_id === requestId);
  }

  function orgKey(row: AdminOrgSummary): SortKey {
    return [row.name, row.org_id];
  }

  /** Every operator write is audited, so each one needs a reason a person actually typed. */
  function badAuditReason(reason: unknown): string | null {
    if (typeof reason !== "string" || reason.trim() === "") return "this operation needs a reason";
    if (reason.length > MAX_GRANT_REASON_CHARS) {
      return `the reason must be at most ${MAX_GRANT_REASON_CHARS} characters`;
    }
    return null;
  }

  function badEntitlementLimits(limits: unknown): string | null {
    if (typeof limits !== "object" || limits === null || Array.isArray(limits)) {
      return "limits must be an object";
    }
    for (const [name, value] of Object.entries(limits)) {
      if (value === undefined) continue;
      if (!(ENTITLEMENT_LIMIT_NAMES as readonly string[]).includes(name)) {
        return `${name} is not an entitlement limit`;
      }
      if (!Number.isInteger(value) || (value as number) < 0 || (value as number) > MAX_ENTITLEMENT_LIMIT) {
        return `${name} must be an integer between 0 and ${MAX_ENTITLEMENT_LIMIT}`;
      }
    }
    return null;
  }

  function summaryOf(org: OrgState, balance: WalletBalance | null, cutoff: number): AdminOrgSummary {
    return {
      org_id: org.org_id,
      name: org.name,
      owner_email: org.owner_email,
      created_at: org.created_at,
      suspended: org.suspended,
      suspension_reason: org.suspension_reason,
      // Only reached with a balance in hand; the callers check for the overflow case first.
      balance: balance ?? { ledger_total: ZERO_MONEY, reserved_total: ZERO_MONEY, available: ZERO_MONEY },
      requests_30d: org.usage.filter((row) => instant(row.created_at) >= cutoff).length,
      entitlements: structuredClone(org.entitlements),
    };
  }

  /** For operator-authority operations only: the tenant comes from the row, not from a caller field. */
  function traceAnywhere(requestId: string): { org: OrgState; trace: TraceDetail } | undefined {
    if (typeof requestId !== "string" || requestId === "") return undefined;
    for (const org of orgs.values()) {
      const trace = ownedTrace(org, requestId);
      if (trace !== undefined) return { org, trace };
    }
    return undefined;
  }

  /**
   * The one place a feedback entry is created. Provenance is a parameter of *this* function, so
   * every caller has to state it explicitly and `feedback.submit` cannot acquire an operator label
   * by accident.
   */
  function appendFeedback(
    org: OrgState,
    trace: TraceDetail,
    provenance: {
      name: FeedbackEntry["name"];
      value: FeedbackValue;
      comment: string | null;
      author_role: FeedbackEntry["author_role"];
      author_principal: string;
      calibration_set: boolean;
      rubric_version: number | null;
    },
  ): FeedbackEntry {
    org.feedbackCounter += 1;
    const entry: FeedbackEntry = {
      id: feedbackId(org.namespace, org.feedbackCounter),
      request_id: trace.request_id,
      created_at: nextTimestamp(),
      channel: "console",
      ...provenance,
    };
    trace.feedback.push(entry);
    trace.feedback_count = trace.feedback.length;
    return structuredClone(entry);
  }

  const services: FakeConsoleServices = {
    sessions: {
      owner: sessionOf(orgsFixture.sessions.owner),
      member: sessionOf(orgsFixture.sessions.member),
      operator: sessionOf(orgsFixture.sessions.operator),
      otherOwner: sessionOf(orgsFixture.sessions.otherOwner),
      suspendedOwner: sessionOf(orgsFixture.sessions.suspendedOwner),
    },
    ids: (() => {
      const first = orgsFixture.orgs[0];
      const second = orgsFixture.orgs[1];
      const orgA = orgs.get(first.org_id);
      const orgB = orgs.get(second.org_id);
      const suspended = [...orgs.values()].find((org) => org.suspended);
      if (orgA === undefined || orgB === undefined) throw new Error("fixture organizations missing");
      if (suspended === undefined) throw new Error("fixtures must contain a suspended organization");
      const available = orgA.traces.find((trace) => trace.content === "available");
      const off = orgA.traces.find((trace) => trace.content === "off");
      if (available === undefined || off === undefined) {
        throw new Error("fixtures must contain an available and an off-mode trace");
      }
      return {
        orgId: orgA.org_id,
        otherOrgId: orgB.org_id,
        availableRequestId: available.request_id,
        offRequestId: off.request_id,
        otherOrgRequestId: orgB.traces[0].request_id,
        unknownRequestId: deterministicUuid(999, 999),
        keyId: orgA.keys[0].id,
        otherOrgKeyId: orgB.keys[0].id,
        suspendedOrgId: suspended.org_id,
      };
    })(),

    failNext(operation, code, message = "injected failure", phase = "before") {
      const queue = injected.get(operation) ?? [];
      queue.push({ code, message, phase });
      injected.set(operation, queue);
    },

    unsafeDebugState() {
      return { orgs: [...orgs.values()], idempotency: [...idempotency.entries()] };
    },

    async usage(session, query) {
      const injectedResult = intercept<Page<UsageRow>>("usage");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<Page<UsageRow>>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<Page<UsageRow>>("invalid_request", invalid);
      const resolved = tenant<Page<UsageRow>>(session);
      if (isError(resolved)) return resolved;
      const rows = usageRowsFor(resolved.org, query);
      return paginate(rows, query, scopeOf(resolved.org, "usage", query), usageKey);
    },

    async usageSummary(session, query) {
      const injectedResult = intercept<UsageSummary>("usageSummary");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<UsageSummary>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<UsageSummary>("invalid_request", invalid);
      const resolved = tenant<UsageSummary>(session);
      if (isError(resolved)) return resolved;
      const rows = usageRowsFor(resolved.org, query);
      // Units, not `addMoney`: a read must not throw because a *sum* left the money domain, even
      // though each row is inside it (N8).
      let costUnits = BigInt(0);
      let heldUnits = BigInt(0);
      let prompt = 0;
      let completion = 0;
      let failed = 0;
      let absorbed = 0;
      for (const row of rows) {
        costUnits += moneyUnits(row.cost);
        prompt += row.prompt_tokens ?? 0;
        completion += row.completion_tokens ?? 0;
        if (row.http_status >= 400) failed += 1;
        if (row.settlement_state === "released_platform_absorbed") absorbed += 1;
        // Everything still held: a non-terminal row has no settlement state yet (R13), so the
        // hold itself is what says "awaiting reconciliation", not the settlement column.
        if (row.usage_certainty === "unknown" && row.max_hold !== null) heldUnits += moneyUnits(row.max_hold);
      }
      const cost = tryMoneyFromUnits(costUnits);
      const pending = tryMoneyFromUnits(heldUnits);
      if (cost === null || pending === null) {
        return fail<UsageSummary>("internal_error", "this usage total does not fit the money domain");
      }
      return ok({
        requests: rows.length,
        failed_requests: failed,
        prompt_tokens: prompt,
        completion_tokens: completion,
        cost,
        pending_reconciliation: pending,
        platform_absorbed_requests: absorbed,
      });
    },

    async usageDaily(session, query) {
      const injectedResult = intercept<UsageDay[]>("usageDaily");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<UsageDay[]>(query, USAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<UsageDay[]>("invalid_request", invalid);
      const resolved = tenant<UsageDay[]>(session);
      if (isError(resolved)) return resolved;
      const days = new Map<string, UsageDay & { units: bigint }>();
      for (const row of usageRowsFor(resolved.org, query)) {
        const day = row.created_at.slice(0, 10);
        const existing = days.get(day) ?? {
          day,
          requests: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          cost: ZERO_MONEY,
          units: BigInt(0),
        };
        existing.requests += 1;
        existing.prompt_tokens += row.prompt_tokens ?? 0;
        existing.completion_tokens += row.completion_tokens ?? 0;
        existing.units += moneyUnits(row.cost);
        days.set(day, existing);
      }
      const out: UsageDay[] = [];
      for (const day of [...days.values()].sort((a, b) => b.day.localeCompare(a.day))) {
        const cost = tryMoneyFromUnits(day.units);
        if (cost === null) return fail<UsageDay[]>("internal_error", "a daily total does not fit the money domain");
        out.push({
          day: day.day,
          requests: day.requests,
          prompt_tokens: day.prompt_tokens,
          completion_tokens: day.completion_tokens,
          cost,
        });
      }
      return ok(out);
    },

    async balances(session) {
      const injectedResult = intercept<WalletBalance>("balances");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<WalletBalance>(session);
      if (isError(resolved)) return resolved;
      const balance = balanceOf(resolved.org);
      if (balance === null) return fail<WalletBalance>("internal_error", "this wallet does not fit the money domain");
      return ok(balance);
    },

    async ledger(session, query) {
      const injectedResult = intercept<Page<LedgerEntry>>("ledger");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<Page<LedgerEntry>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const resolved = tenant<Page<LedgerEntry>>(session);
      if (isError(resolved)) return resolved;
      return paginate(resolved.org.ledger, query, scopeOf(resolved.org, "ledger", query), timeKey);
    },

    async traces(session, query) {
      const injectedResult = intercept<Page<TraceListItem>>("traces");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<Page<TraceListItem>>(query, TRACE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const invalid = badFilter(query);
      if (invalid !== null) return fail<Page<TraceListItem>>("invalid_request", invalid);
      const resolved = tenant<Page<TraceListItem>>(session);
      if (isError(resolved)) return resolved;
      const rows = traceRowsFor(resolved.org, query);
      return paginate(rows, query, scopeOf(resolved.org, "traces", query), traceKey);
    },

    async traceDetail(session, requestId) {
      const injectedResult = intercept<TraceDetail>("traceDetail");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<TraceDetail>(session);
      if (isError(resolved)) return resolved;
      const trace = ownedTrace(resolved.org, requestId);
      if (trace === undefined) return fail("not_found", "no such request for this organization");
      return ok(structuredClone(trace));
    },

    async traceContent(session, requestId) {
      const injectedResult = intercept<TraceContentView>("traceContent");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<TraceContentView>(session);
      if (isError(resolved)) return resolved;
      if (ownedTrace(resolved.org, requestId) === undefined) {
        return fail("not_found", "no such request for this organization");
      }
      const view = resolved.org.content.get(requestId);
      if (view === undefined) return fail("not_found", "no such request for this organization");
      return ok(structuredClone(view));
    },

    feedback: {
      async list(session, requestId) {
        const injectedResult = intercept<FeedbackEntry[]>("feedback.list");
        if (injectedResult !== null) return injectedResult;
        const resolved = tenant<FeedbackEntry[]>(session);
        if (isError(resolved)) return resolved;
        const trace = ownedTrace(resolved.org, requestId);
        if (trace === undefined) return fail("not_found", "no such request for this organization");
        return ok(structuredClone(trace.feedback));
      },

      async submit(session, input) {
        const injectedResult = intercept<FeedbackEntry>("feedback.submit");
        if (injectedResult !== null) return injectedResult;
        const rejected = badInput<FeedbackEntry>(input, FEEDBACK_INPUT_FIELDS);
        if (rejected !== null) return rejected;
        const resolved = tenant<FeedbackEntry>(session);
        if (isError(resolved)) return resolved;
        const badKey = badIdempotencyKey(input.idempotency_key, true);
        if (badKey !== null) return fail<FeedbackEntry>("invalid_request", badKey);
        const badBody = badFeedbackBody(input);
        if (badBody !== null) return fail<FeedbackEntry>("invalid_request", badBody);
        const trace = ownedTrace(resolved.org, input.request_id);
        if (trace === undefined) return fail("not_found", "no such request for this organization");

        const comment = input.comment ?? null;
        const payload = canonicalPayload({
          request_id: trace.request_id,
          name: input.name,
          value: input.value,
          comment,
        });
        const replay = replayOf<FeedbackEntry>(session, "feedback.submit", input.idempotency_key, payload);
        if (replay !== null) return replay;

        // Everything above can refuse; from here nothing can, so the state cannot half-change.
        // Provenance is server-set, and it does not consult the session's authority: an operator
        // pressing the console's own feedback control is a customer signal, because the control is
        // the customer's (02: "console-origin input is not automatically an operator label";
        // R19). An operator label comes from `calibration.label` and nowhere else.
        return commit(
          "feedback.submit",
          session,
          input.idempotency_key,
          payload,
          () => appendFeedback(resolved.org, trace, {
            name: input.name,
            value: input.value,
            comment,
            author_role: "customer",
            author_principal: session.email,
            calibration_set: false,
            rubric_version: null,
          }),
        );
      },
    },

    calibration: {
      async label(session, input) {
        const injectedResult = intercept<FeedbackEntry>("calibration.label");
        if (injectedResult !== null) return injectedResult;
        const rejected = badInput<FeedbackEntry>(input, CALIBRATION_LABEL_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireOperator<FeedbackEntry>(session);
        if (denied !== null) return denied;
        const badKey = badIdempotencyKey(input.idempotency_key, true);
        if (badKey !== null) return fail<FeedbackEntry>("invalid_request", badKey);
        if (!(CALIBRATION_LABELS as readonly string[]).includes(input.label)) {
          return fail("invalid_request", `label must be one of ${CALIBRATION_LABELS.join(", ")}`);
        }
        if (
          !Number.isInteger(input.rubric_version) ||
          input.rubric_version < 1 ||
          input.rubric_version > MAX_RUBRIC_VERSION
        ) {
          return fail("invalid_request", `rubric_version must be an integer between 1 and ${MAX_RUBRIC_VERSION}`);
        }
        const comment = input.comment ?? null;
        if (comment !== null && (typeof comment !== "string" || comment.trim() === "")) {
          return fail("invalid_request", "comment must be non-empty text when present");
        }
        if (comment !== null && comment.length > MAX_FEEDBACK_TEXT_CHARS) {
          return fail("invalid_request", `comment must be at most ${MAX_FEEDBACK_TEXT_CHARS} characters`);
        }
        // An operator labels a request of *some* organization, so the trace is looked up across
        // organizations — the operator flag is the authority, and the label records which
        // organization it landed in through the trace it names.
        const found = traceAnywhere(input.request_id);
        if (found === undefined) return fail("not_found", "no such request");

        const payload = canonicalPayload({
          request_id: input.request_id,
          rubric_version: input.rubric_version,
          label: input.label,
          comment,
        });
        const replay = replayOf<FeedbackEntry>(session, "calibration.label", input.idempotency_key, payload);
        if (replay !== null) return replay;

        return commit(
          "calibration.label",
          session,
          input.idempotency_key,
          payload,
          () => appendFeedback(found.org, found.trace, {
            name: "calibration_label",
            value: input.label as CalibrationLabelValue,
            comment,
            author_role: "operator",
            author_principal: session.email,
            calibration_set: true,
            rubric_version: input.rubric_version,
          }),
        );
      },

      async list(session, query) {
        const injectedResult = intercept<Page<FeedbackEntry>>("calibration.list");
        if (injectedResult !== null) return injectedResult;
        const rejected = badInput<Page<FeedbackEntry>>(query, PAGE_QUERY_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireOperator<Page<FeedbackEntry>>(session);
        if (denied !== null) return denied;
        const rows: FeedbackEntry[] = [];
        for (const org of orgs.values()) {
          for (const trace of org.traces) {
            for (const entry of trace.feedback) if (entry.calibration_set) rows.push(entry);
          }
        }
        rows.sort((a, b) => -compareKeys(timeKey(a), timeKey(b)));
        return paginate(rows, query, "operators|calibration.list", timeKey);
      },
    },

    settings: {
      async get(session) {
        const injectedResult = intercept<ConsoleSettings>("settings.get");
        if (injectedResult !== null) return injectedResult;
        const resolved = tenant<ConsoleSettings>(session);
        if (isError(resolved)) return resolved;
        return ok(structuredClone(resolved.org.settings));
      },

      async update(session, update) {
        const injectedResult = intercept<ConsoleSettings>("settings.update");
        if (injectedResult !== null) return injectedResult;
        const rejected = badInput<ConsoleSettings>(update, SETTINGS_UPDATE_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireOwner<ConsoleSettings>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ConsoleSettings>(session);
        if (isError(resolved)) return resolved;
        const badKey = badIdempotencyKey(update.idempotency_key, false);
        if (badKey !== null) return fail<ConsoleSettings>("invalid_request", badKey);
        if (update.trace_mode !== undefined && !(TRACE_MODES as readonly string[]).includes(update.trace_mode)) {
          return fail("invalid_request", "trace_mode must be off, minimal or full");
        }
        if (update.content_retention_days !== undefined) {
          const days = update.content_retention_days;
          if (!Number.isInteger(days) || days < 1 || days > MAX_CONTENT_RETENTION_DAYS) {
            return fail(
              "invalid_request",
              `content_retention_days must be an integer between 1 and ${MAX_CONTENT_RETENTION_DAYS}`,
            );
          }
        }
        if (update.evaluation_consent !== undefined && typeof update.evaluation_consent !== "boolean") {
          return fail("invalid_request", "evaluation_consent must be a boolean");
        }
        const payload = canonicalPayload({
          trace_mode: update.trace_mode,
          content_retention_days: update.content_retention_days,
          evaluation_consent: update.evaluation_consent,
        });
        const replay = replayOf<ConsoleSettings>(session, "settings.update", update.idempotency_key, payload);
        if (replay !== null) return replay;

        return commit("settings.update", session, update.idempotency_key, payload, () => {
          const settings = resolved.org.settings;
          if (update.trace_mode !== undefined) settings.trace_mode = update.trace_mode;
          if (update.content_retention_days !== undefined) {
            settings.content_retention_days = update.content_retention_days;
          }
          // Consent is an independent control (DEC-10): it changes only when asked to, and
          // every change keeps an audit entry.
          if (update.evaluation_consent !== undefined && update.evaluation_consent !== settings.evaluation_consent) {
            settings.evaluation_consent = update.evaluation_consent;
            settings.consent_history.push({
              changed_at: nextTimestamp(),
              evaluation_consent: update.evaluation_consent,
              changed_by: session.email,
            });
          }
          return structuredClone(settings);
        });
      },
    },

    keys: {
      async list(session) {
        const injectedResult = intercept<ApiKeySummary[]>("keys.list");
        if (injectedResult !== null) return injectedResult;
        const resolved = tenant<ApiKeySummary[]>(session);
        if (isError(resolved)) return resolved;
        return ok(structuredClone(resolved.org.keys));
      },

      async create(session, input) {
        const injectedResult = intercept<ApiKeyCreated>("keys.create");
        if (injectedResult !== null) return injectedResult;
        const rejected = badInput<ApiKeyCreated>(input, API_KEY_CREATE_FIELDS);
        if (rejected !== null) return rejected;
        const denied = requireOwner<ApiKeyCreated>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ApiKeyCreated>(session);
        if (isError(resolved)) return resolved;
        const badKey = badIdempotencyKey(input.idempotency_key, false);
        if (badKey !== null) return fail<ApiKeyCreated>("invalid_request", badKey);
        if (typeof input.name !== "string" || input.name.trim() === "") {
          return fail("invalid_request", "a key needs a name");
        }
        if (input.name.length > MAX_KEY_NAME_CHARS) {
          return fail("invalid_request", `a key name must be at most ${MAX_KEY_NAME_CHARS} characters`);
        }
        if (input.trace_mode !== undefined && !(TRACE_MODES as readonly string[]).includes(input.trace_mode)) {
          return fail("invalid_request", "trace_mode must be off, minimal or full");
        }
        const name = input.name.trim();
        const trace_mode = input.trace_mode ?? resolved.org.settings.trace_mode;
        const payload = canonicalPayload({ name, trace_mode });
        // R16: a secret is shown exactly once, to the first response. The record holds the key's
        // *id* and nothing else, so a replay — by the same user, another owner or an operator —
        // rereads the key's current metadata and returns `secret: null`. There is no stored secret
        // for any authorization to unlock.
        const replayedId = replayOf<string>(session, "keys.create", input.idempotency_key, payload);
        if (replayedId !== null) {
          if (!replayedId.ok) return replayedId as unknown as Result<ApiKeyCreated>;
          const existing = resolved.org.keys.find((candidate) => candidate.id === replayedId.value);
          if (existing === undefined) return fail("not_found", "no such key for this organization");
          return ok({ ...structuredClone(existing), secret: null, replayed: true });
        }

        return commit(
          "keys.create",
          session,
          input.idempotency_key,
          payload,
          () => {
            resolved.org.counter += 1;
            const suffix = hex(resolved.org.counter * 7919, 8);
            const summary: ApiKeySummary = {
              id: deterministicUuid(4242 + resolved.org.namespace, resolved.org.counter),
              name,
              prefix: `sk-infrx-${suffix}`,
              created_at: nextTimestamp(),
              last_used_at: null,
              revoked_at: null,
              trace_mode,
            };
            resolved.org.keys.push(summary);
            // Fixture secret: presented once and held only in this response object, never a real
            // credential. Nothing writes it to the key record or to the idempotency record.
            return {
              ...structuredClone(summary),
              secret: `sk-infrx-FAKE${suffix}${hex(resolved.org.counter, 24)}`,
              replayed: false,
            };
          },
          (created) => created.id,
        );
      },

      async revoke(session, keyId, idempotencyKey) {
        const injectedResult = intercept<ApiKeySummary>("keys.revoke");
        if (injectedResult !== null) return injectedResult;
        const denied = requireOwner<ApiKeySummary>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ApiKeySummary>(session);
        if (isError(resolved)) return resolved;
        const badKey = badIdempotencyKey(idempotencyKey, false);
        if (badKey !== null) return fail<ApiKeySummary>("invalid_request", badKey);
        if (typeof keyId !== "string" || keyId === "") {
          return fail("invalid_request", "a key id is required");
        }
        const key = resolved.org.keys.find((candidate) => candidate.id === keyId);
        if (key === undefined) return fail("not_found", "no such key for this organization");
        const payload = canonicalPayload({ key_id: keyId });
        const replay = replayOf<ApiKeySummary>(session, "keys.revoke", idempotencyKey, payload);
        if (replay !== null) return replay;
        if (key.revoked_at !== null) return fail("state_conflict", "this key is already revoked");

        return commit("keys.revoke", session, idempotencyKey, payload, () => {
          key.revoked_at = nextTimestamp();
          return structuredClone(key);
        });
      },
    },

    async adminOrgs(session, query) {
      const injectedResult = intercept<Page<AdminOrgSummary>>("adminOrgs");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<Page<AdminOrgSummary>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireOperator<Page<AdminOrgSummary>>(session);
      if (denied !== null) return denied;
      const cutoff = clockMs - 30 * 86400000;
      const rows: AdminOrgSummary[] = [];
      for (const org of orgs.values()) {
        const balance = balanceOf(org);
        if (balance === null) {
          return fail<Page<AdminOrgSummary>>("internal_error", "a wallet does not fit the money domain");
        }
        rows.push(summaryOf(org, balance, cutoff));
      }
      // Ascending by (name, org_id): the one list whose order is not newest-first, and the reason
      // `paginate` takes a direction.
      rows.sort((a, b) => compareKeys(orgKey(a), orgKey(b)));
      return paginate(rows, query, "operators|adminOrgs", orgKey, false);
    },

    async adminGrant(session, input) {
      const injectedResult = intercept<AdminGrantResult>("adminGrant");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<AdminGrantResult>(input, ADMIN_GRANT_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireOperator<AdminGrantResult>(session);
      if (denied !== null) return denied;
      const badKey = badIdempotencyKey(input.idempotency_key, true);
      if (badKey !== null) return fail<AdminGrantResult>("invalid_request", badKey);
      if (typeof input.reason !== "string" || input.reason.trim() === "") {
        return fail("invalid_request", "a grant needs a reason");
      }
      if (input.reason.length > MAX_GRANT_REASON_CHARS) {
        return fail("invalid_request", `a grant reason must be at most ${MAX_GRANT_REASON_CHARS} characters`);
      }
      if (input.kind !== "promotional") {
        return fail("invalid_request", "only promotional grants exist in the free pilot");
      }
      // R11: negative, zero, non-decimal and over-scale amounts are refused at the boundary.
      const units = tryParseMoneyUnits(input.amount);
      if (units === null) {
        return fail("invalid_request", "amount must be a USD decimal string within numeric(20, 8)");
      }
      if (units <= BigInt(0)) return fail("invalid_request", "a grant amount must be positive");
      const amount = moneyFromUnits(units);
      const target = orgs.get(input.target_org_id);
      if (target === undefined) return fail("not_found", "no such organization");

      const reason = input.reason.trim();
      const payload = canonicalPayload({
        target_org_id: input.target_org_id,
        amount,
        kind: input.kind,
        reason,
      });
      const replay = replayOf<AdminGrantResult>(session, "adminGrant", input.idempotency_key, payload);
      if (replay !== null) {
        if (!replay.ok) return replay;
        const current = balanceOf(target);
        if (current === null) {
          return fail<AdminGrantResult>("internal_error", "this wallet does not fit the money domain");
        }
        return ok({ ...replay.value, replayed: true, balance: current });
      }
      // The whole point of the pre-check: a total that would leave numeric(20, 8) is refused
      // *before* the entry exists, so nothing is appended, no idempotency record is written, and
      // the retry that follows still grants exactly once.
      const projected = balanceOf(target, units);
      if (projected === null) {
        return fail("invalid_request", "this grant would take the wallet outside numeric(20, 8)");
      }

      return commit("adminGrant", session, input.idempotency_key, payload, () => {
        target.counter += 1;
        const entry: LedgerEntry = {
          id: deterministicUuid(8080 + target.namespace, target.counter),
          created_at: nextTimestamp(),
          delta: amount,
          kind: "grant",
          reason,
          // The key is the ledger row's `ref`, so the effect carries its own natural idempotency
          // key: a store that lost the record can still recognise the grant it already made.
          ref: input.idempotency_key,
          actor: session.email,
        };
        target.ledger.unshift(entry);
        target.ledger.sort((a, b) => -compareKeys(timeKey(a), timeKey(b)));
        return {
          grant_id: entry.id,
          org_id: target.org_id,
          amount,
          kind: "promotional" as const,
          reason,
          created_at: entry.created_at,
          operator_principal: session.email,
          replayed: false,
          balance: projected,
        };
      });
    },

    async adminSetSuspension(session, input) {
      const injectedResult = intercept<AdminOrgSummary>("adminSetSuspension");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<AdminOrgSummary>(input, ADMIN_SUSPENSION_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireOperator<AdminOrgSummary>(session);
      if (denied !== null) return denied;
      const badKey = badIdempotencyKey(input.idempotency_key, true);
      if (badKey !== null) return fail<AdminOrgSummary>("invalid_request", badKey);
      if (typeof input.suspended !== "boolean") {
        return fail("invalid_request", "suspended must be a boolean");
      }
      const badReason = badAuditReason(input.reason);
      if (badReason !== null) return fail<AdminOrgSummary>("invalid_request", badReason);
      const target = orgs.get(input.target_org_id);
      if (target === undefined) return fail("not_found", "no such organization");
      const reason = input.reason.trim();
      const payload = canonicalPayload({
        target_org_id: input.target_org_id,
        suspended: input.suspended,
        reason,
      });
      const replay = replayOf<AdminOrgSummary>(session, "adminSetSuspension", input.idempotency_key, payload);
      if (replay !== null) return replay;

      return commit("adminSetSuspension", session, input.idempotency_key, payload, () => {
        // Suspension gates new work. It does not touch the ledger or a terminal usage row: what was
        // already settled stays settled, because accounting that happened is a fact (R18).
        target.suspended = input.suspended;
        target.suspension_reason = reason;
        const cutoff = clockMs - 30 * 86400000;
        return summaryOf(target, balanceOf(target), cutoff);
      });
    },

    async adminSetEntitlements(session, input) {
      const injectedResult = intercept<OrgEntitlements>("adminSetEntitlements");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<OrgEntitlements>(input, ADMIN_ENTITLEMENTS_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireOperator<OrgEntitlements>(session);
      if (denied !== null) return denied;
      const badKey = badIdempotencyKey(input.idempotency_key, true);
      if (badKey !== null) return fail<OrgEntitlements>("invalid_request", badKey);
      const badReason = badAuditReason(input.reason);
      if (badReason !== null) return fail<OrgEntitlements>("invalid_request", badReason);
      if (!Array.isArray(input.model_ids) || input.model_ids.length > 100) {
        return fail("invalid_request", "model_ids must be a list of at most 100 model identifiers");
      }
      for (const model of input.model_ids) {
        if (typeof model !== "string" || model.trim() === "" || model.length > 200) {
          return fail("invalid_request", "each model id must be a non-empty identifier");
        }
      }
      if (new Set(input.model_ids).size !== input.model_ids.length) {
        return fail("invalid_request", "model_ids must not repeat");
      }
      const badLimit = badEntitlementLimits(input.limits);
      if (badLimit !== null) return fail<OrgEntitlements>("invalid_request", badLimit);
      const target = orgs.get(input.target_org_id);
      if (target === undefined) return fail("not_found", "no such organization");

      const model_ids = [...input.model_ids].sort();
      const limits: Partial<Record<EntitlementLimitName, number>> = {};
      for (const name of ENTITLEMENT_LIMIT_NAMES) {
        const value = input.limits[name];
        if (value !== undefined) limits[name] = value;
      }
      const payload = canonicalPayload({
        target_org_id: input.target_org_id,
        model_ids: model_ids.join(","),
        limits: canonicalPayload(limits),
        reason: input.reason.trim(),
      });
      const replay = replayOf<OrgEntitlements>(session, "adminSetEntitlements", input.idempotency_key, payload);
      if (replay !== null) return replay;

      return commit("adminSetEntitlements", session, input.idempotency_key, payload, () => {
        target.entitlements = {
          org_id: target.org_id,
          model_ids,
          limits,
          updated_at: nextTimestamp(),
          updated_by: session.email,
        };
        return structuredClone(target.entitlements);
      });
    },

    async judgeRuns(session, query) {
      const injectedResult = intercept<Page<JudgeRun>>("judgeRuns");
      if (injectedResult !== null) return injectedResult;
      const rejected = badInput<Page<JudgeRun>>(query, PAGE_QUERY_FIELDS);
      if (rejected !== null) return rejected;
      const denied = requireOwnerOrOperator<Page<JudgeRun>>(session);
      if (denied !== null) return denied;
      const resolved = tenant<Page<JudgeRun>>(session);
      if (isError(resolved)) return resolved;
      return paginate(resolved.org.judge, query, scopeOf(resolved.org, "judgeRuns", query), timeKey);
    },
  };

  return services;
}
