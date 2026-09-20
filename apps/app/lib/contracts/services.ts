/**
 * The console service boundary — the named operations of 08 §9.
 *
 * Rules the interface itself enforces, so C, U and V cannot drift apart:
 * - every operation takes the trusted `SessionContext` first; the tenant is
 *   `session.orgId` and nothing else. No operation accepts an org id (except the
 *   operator-only grant target, checked against `session.isOperator`), an author
 *   role, a feedback channel or a storage key from the caller.
 * - lists return `Page<T>` with an opaque cursor and reject `limit > 100`.
 * - failures are returned as `Result` errors with the codes of 08 §3, not thrown. That includes
 *   an amount outside the money domain and a total that would leave it: an operation validates
 *   everything before it writes anything, so a refused call leaves no trace and a retry under the
 *   same idempotency key still produces exactly one effect.
 * - an unknown field in any input is `invalid_request`, mirroring pydantic `extra="forbid"` on
 *   the Python side: a smuggled `org_id`, `author_role`, `channel` or storage key is refused, not
 *   quietly dropped.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import type {
  AdminGrantInput,
  AdminGrantResult,
  AdminOrgSummary,
  ApiKeyCreateInput,
  ApiKeyCreated,
  ApiKeySummary,
  ConsoleSettings,
  FeedbackEntry,
  FeedbackInput,
  JudgeRun,
  LedgerEntry,
  Page,
  PageQuery,
  Result,
  SessionContext,
  SettingsUpdate,
  TraceContentView,
  TraceDetail,
  TraceListItem,
  TraceQuery,
  UsageDay,
  UsageQuery,
  UsageRow,
  UsageSummary,
  WalletBalance,
} from "./types.ts";

export interface ConsoleServices {
  usage(session: SessionContext, query: UsageQuery): Promise<Result<Page<UsageRow>>>;
  usageSummary(session: SessionContext, query: UsageQuery): Promise<Result<UsageSummary>>;
  usageDaily(session: SessionContext, query: UsageQuery): Promise<Result<UsageDay[]>>;

  balances(session: SessionContext): Promise<Result<WalletBalance>>;
  ledger(session: SessionContext, query: PageQuery): Promise<Result<Page<LedgerEntry>>>;

  traces(session: SessionContext, query: TraceQuery): Promise<Result<Page<TraceListItem>>>;
  traceDetail(session: SessionContext, requestId: string): Promise<Result<TraceDetail>>;
  traceContent(session: SessionContext, requestId: string): Promise<Result<TraceContentView>>;

  feedback: {
    list(session: SessionContext, requestId: string): Promise<Result<FeedbackEntry[]>>;
    submit(session: SessionContext, input: FeedbackInput): Promise<Result<FeedbackEntry>>;
  };

  settings: {
    get(session: SessionContext): Promise<Result<ConsoleSettings>>;
    update(session: SessionContext, update: SettingsUpdate): Promise<Result<ConsoleSettings>>;
  };

  /**
   * Keys are not in the 08 §9 list; U2 needs them behind the same boundary and the
   * same role checks, so F2 declares them here (recorded as an amendment request).
   */
  keys: {
    list(session: SessionContext): Promise<Result<ApiKeySummary[]>>;
    create(session: SessionContext, input: ApiKeyCreateInput): Promise<Result<ApiKeyCreated>>;
    revoke(session: SessionContext, keyId: string, idempotencyKey?: string): Promise<Result<ApiKeySummary>>;
  };

  adminOrgs(session: SessionContext, query: PageQuery): Promise<Result<Page<AdminOrgSummary>>>;
  adminGrant(session: SessionContext, input: AdminGrantInput): Promise<Result<AdminGrantResult>>;

  /** Owner and platform operator only (R13); a member cannot read evaluation runs. */
  judgeRuns(session: SessionContext, query: PageQuery): Promise<Result<Page<JudgeRun>>>;
}

/** Operation names, for failure injection and for coverage assertions in the suite. */
export const CONSOLE_OPERATIONS = [
  "usage",
  "usageSummary",
  "usageDaily",
  "balances",
  "ledger",
  "traces",
  "traceDetail",
  "traceContent",
  "feedback.list",
  "feedback.submit",
  "settings.get",
  "settings.update",
  "keys.list",
  "keys.create",
  "keys.revoke",
  "adminOrgs",
  "adminGrant",
  "judgeRuns",
] as const;
export type ConsoleOperation = (typeof CONSOLE_OPERATIONS)[number];

/** Operations that mutate tenant state: owner-only, never a member. */
export const OWNER_ONLY_OPERATIONS = [
  "settings.update",
  "keys.create",
  "keys.revoke",
] as const satisfies readonly ConsoleOperation[];

/** Operations that require platform-operator authority. */
export const OPERATOR_ONLY_OPERATIONS = [
  "adminOrgs",
  "adminGrant",
] as const satisfies readonly ConsoleOperation[];

/** Readable by the organization owner or a platform operator, never a member (R13). */
export const OWNER_OR_OPERATOR_OPERATIONS = ["judgeRuns"] as const satisfies readonly ConsoleOperation[];

/**
 * Operations that write. Each one validates completely before it mutates, and each one accepts an
 * idempotency key scoped to (caller organization, operation, target organization, payload) —
 * required on `adminGrant` and `feedback.submit` (R3/R13), optional on the rest.
 */
export const MUTATING_OPERATIONS = [
  "feedback.submit",
  "settings.update",
  "keys.create",
  "keys.revoke",
  "adminGrant",
] as const satisfies readonly ConsoleOperation[];
