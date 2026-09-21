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
  FEEDBACK_INPUT_FIELDS,
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
import { cursorScope, decodeCursor, encodeCursor } from "./cursor.ts";
import {
  buildPlan,
  feedbackCountPredicate,
  type Keyset,
  type NamedQueryName,
  type Predicate,
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

const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

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

function text(row: Row, column: string): string {
  const value = row[column];
  return typeof value === "string" ? value : String(value ?? "");
}

function optionalText(row: Row, column: string): string | null {
  const value = row[column];
  return value === null || value === undefined ? null : String(value);
}

function integer(row: Row, column: string): number {
  return Number(row[column] ?? 0);
}

function optionalInteger(row: Row, column: string): number | null {
  const value = row[column];
  return value === null || value === undefined ? null : Number(value);
}

function flag(row: Row, column: string): boolean {
  return row[column] === true || row[column] === 1 || row[column] === "true";
}

function money(row: Row, column: string): Money {
  const value = row[column];
  if (value === null || value === undefined) return ZERO_MONEY;
  return parseMoney(typeof value === "string" ? value : String(value));
}

function optionalMoney(row: Row, column: string): Money | null {
  const value = row[column];
  if (value === null || value === undefined) return null;
  return parseMoney(typeof value === "string" ? value : String(value));
}

function json(row: Row, column: string): unknown {
  const value = row[column];
  if (typeof value !== "string") return value ?? null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function usageRowOf(row: Row): UsageRow {
  return {
    request_id: text(row, "request_id"),
    created_at: text(row, "created_at"),
    model: text(row, "model"),
    key_id: text(row, "key_id"),
    key_name: text(row, "key_name"),
    execution_mode: text(row, "execution_mode") as UsageRow["execution_mode"],
    job_state: text(row, "job_state") as UsageRow["job_state"],
    terminal_cause: optionalText(row, "terminal_cause") as UsageRow["terminal_cause"],
    http_status: integer(row, "http_status"),
    prompt_tokens: optionalInteger(row, "prompt_tokens"),
    completion_tokens: optionalInteger(row, "completion_tokens"),
    usage_certainty: text(row, "usage_certainty") as UsageRow["usage_certainty"],
    settlement_state: optionalText(row, "settlement_state") as UsageRow["settlement_state"],
    cost: money(row, "cost"),
    max_hold: optionalMoney(row, "max_hold"),
    trace_mode: text(row, "trace_mode") as UsageRow["trace_mode"],
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
    created_at: text(row, "created_at"),
    delta: money(row, "delta"),
    kind: text(row, "kind") as LedgerEntry["kind"],
    reason: optionalText(row, "reason"),
    ref: optionalText(row, "ref"),
    actor: flag(row, "by_operator") && !session.isOperator ? PLATFORM_ACTOR : actor,
  };
}

function keyOf(row: Row): ApiKeySummary {
  return {
    id: text(row, "id"),
    name: text(row, "name"),
    prefix: text(row, "prefix"),
    created_at: text(row, "created_at"),
    last_used_at: optionalText(row, "last_used_at"),
    revoked_at: optionalText(row, "revoked_at"),
    trace_mode: text(row, "trace_mode") as ApiKeySummary["trace_mode"],
  };
}

function traceListItemOf(row: Row): TraceListItem {
  return {
    request_id: text(row, "request_id"),
    created_at: text(row, "created_at"),
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
    content_expires_at: optionalText(row, "content_expires_at"),
    metadata_expires_at: text(row, "metadata_expires_at"),
    feedback,
  };
}

/** R41 for feedback: the entry stays a customer signal; only the operator's identity is withheld. */
function feedbackEntryOf(row: Row, session: SessionContext): FeedbackEntry {
  const rawValue = row["value"];
  const value =
    typeof rawValue === "boolean" || typeof rawValue === "number" || typeof rawValue === "string"
      ? rawValue
      : String(rawValue ?? "");
  return {
    id: text(row, "id"),
    request_id: text(row, "request_id"),
    created_at: text(row, "created_at"),
    channel: text(row, "channel") as FeedbackEntry["channel"],
    author_role: text(row, "author_role") as FeedbackEntry["author_role"],
    author_principal:
      flag(row, "by_operator") && !session.isOperator ? PLATFORM_ACTOR : text(row, "author_principal"),
    name: text(row, "name") as FeedbackEntry["name"],
    value,
    comment: optionalText(row, "comment"),
    calibration_set: flag(row, "calibration_set"),
    rubric_version: optionalInteger(row, "rubric_version"),
  };
}

function consentEntryOf(row: Row, session: SessionContext): ConsentHistoryEntry {
  return {
    changed_at: text(row, "changed_at"),
    evaluation_consent: flag(row, "evaluation_consent"),
    changed_by: flag(row, "by_operator") && !session.isOperator ? PLATFORM_ACTOR : text(row, "changed_by"),
  };
}

function entitlementsOf(row: Row): OrgEntitlements {
  const modelIds = json(row, "model_ids");
  const limitsValue = json(row, "limits");
  const limits: Partial<Record<EntitlementLimitName, number>> = {};
  if (typeof limitsValue === "object" && limitsValue !== null) {
    for (const [name, value] of Object.entries(limitsValue as Record<string, unknown>)) {
      limits[name as EntitlementLimitName] = Number(value);
    }
  }
  return {
    org_id: text(row, "org_id"),
    model_ids: Array.isArray(modelIds) ? (modelIds as string[]) : null,
    limits,
    updated_at: optionalText(row, "entitlements_updated_at"),
    updated_by: optionalText(row, "entitlements_updated_by"),
  };
}

function walletOf(ledgerTotal: Money, reservedTotal: Money): WalletBalance | null {
  const available = tryMoneyFromUnits(moneyUnits(ledgerTotal) - moneyUnits(reservedTotal));
  if (available === null) return null;
  return { ledger_total: ledgerTotal, reserved_total: reservedTotal, available };
}

function judgeRunOf(row: Row): JudgeRun {
  const samples = json(row, "samples");
  return {
    id: text(row, "id"),
    created_at: text(row, "created_at"),
    state: text(row, "state") as JudgeRun["state"],
    mode: text(row, "mode") as JudgeRun["mode"],
    rubric_version: integer(row, "rubric_version"),
    judge_model: text(row, "judge_model"),
    judge_model_version: text(row, "judge_model_version"),
    sample_count: integer(row, "sample_count"),
    limited_evaluation_count: integer(row, "limited_evaluation_count"),
    budget_reserved: money(row, "budget_reserved"),
    budget_settled: optionalMoney(row, "budget_settled"),
    consent_snapshot_at: text(row, "consent_snapshot_at"),
    external_batch_id: optionalText(row, "external_batch_id"),
    quarantine_reason: optionalText(row, "quarantine_reason"),
    samples: Array.isArray(samples) ? (samples as JudgeSample[]) : [],
  };
}

function auditEntryOf(row: Row): AuditEntry {
  const before = json(row, "before");
  const after = json(row, "after");
  return {
    id: text(row, "id"),
    at: text(row, "at"),
    actor_principal: text(row, "actor_principal"),
    action: text(row, "action") as AuditEntry["action"],
    target_org_id: text(row, "target_org_id"),
    reason: text(row, "reason"),
    before: before === null ? null : (before as Record<string, unknown>),
    after: (after ?? {}) as Record<string, unknown>,
    idempotency_key: text(row, "idempotency_key"),
  };
}

// ---------------------------------------------------------------------------
// The services
// ---------------------------------------------------------------------------

export function createConsoleServices(config: ConsoleServicesConfig): ConsoleServices {
  const { pg, ch, cursorSecret } = config;
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
    if ((OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation) && !session.isOperator) {
      return fail<T>("forbidden", "this operation requires platform-operator authority");
    }
    // Operator authority is the flag; it never stands in for the organization role.
    if ((OWNER_ONLY_OPERATIONS as readonly string[]).includes(operation) && session.role !== "owner") {
      return fail<T>("forbidden", "only an organization owner can change this");
    }
    if (
      (OWNER_OR_OPERATOR_OPERATIONS as readonly string[]).includes(operation) &&
      session.role !== "owner" &&
      !session.isOperator
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
    const found = await portFor(name).run(plan);
    const items = found.slice(0, limit).map(project);
    const more = found.length > limit;
    const lastRow = found[items.length - 1];
    return ok({
      items,
      next_cursor: more && lastRow !== undefined ? encodeCursor(cursorSecret, scope, keyOf(lastRow)) : null,
    });
  }

  const timeKey = (atColumn: string, idColumn: string) => (row: Row): Keyset => ({
    at: text(row, atColumn),
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
      const invalid = badFilter(query);
      if (invalid !== null) return fail<UsageSummary>("invalid_request", invalid);
      const resolved = await tenant<UsageSummary>(session, "usageSummary");
      if (isFailure(resolved)) return resolved;
      const found = await rows("usage_summary", { orgId: resolved.orgId, filters: usageFilters(query) });
      const row = found[0] ?? {};
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
      const invalid = badFilter(query);
      if (invalid !== null) return fail<UsageDay[]>("invalid_request", invalid);
      const resolved = await tenant<UsageDay[]>(session, "usageDaily");
      if (isFailure(resolved)) return resolved;
      const found = await rows("usage_daily", { orgId: resolved.orgId, filters: usageFilters(query) });
      return ok(
        found.map((row) => ({
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
        // Bounded like every other read. `ConsoleSettings.consent_history` is a whole array in the
        // contract, with no cursor, so a history longer than this would be truncated rather than
        // paged — recorded as a limit rather than left as an unbounded scan.
        const history = await rows("consent_history", { orgId: resolved.orgId, limit: MAX_PAGE_LIMIT });
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
