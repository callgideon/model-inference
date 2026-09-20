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
  AdminEntitlementsInput,
  AuditEntry,
  AuditQuery,
  AdminGrantInput,
  AdminGrantResult,
  AdminOrgSummary,
  AdminSuspensionInput,
  ApiKeyCreateInput,
  ApiKeyCreated,
  ApiKeySummary,
  CalibrationLabelInput,
  ConsoleSettings,
  FeedbackEntry,
  FeedbackInput,
  JudgeRun,
  LedgerEntry,
  OrgEntitlements,
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

  /**
   * Ordinary console feedback. Whatever the session, the entry is `channel: "console"`,
   * `author_role: "customer"`, `calibration_set: false`, `rubric_version: null` — an operator
   * pressing the same button is a customer signal (02, R19). Operator labels come from
   * `calibration.label` and nowhere else.
   */
  feedback: {
    list(session: SessionContext, requestId: string): Promise<Result<FeedbackEntry[]>>;
    submit(session: SessionContext, input: FeedbackInput): Promise<Result<FeedbackEntry>>;
  };

  /** Operator-only (R19): the only path to an `operator` author role and calibration membership. */
  calibration: {
    label(session: SessionContext, input: CalibrationLabelInput): Promise<Result<FeedbackEntry>>;
    list(session: SessionContext, query: PageQuery): Promise<Result<Page<FeedbackEntry>>>;
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

  /**
   * Operator-only entitlement controls (R19). Suspension gates new work and configuration changes
   * only (R33): a suspended organization keeps every read and `keys.revoke`, so a leaked key can
   * always be revoked, and no ledger entry or terminal usage row is ever rewritten.
   */
  adminSetSuspension(session: SessionContext, input: AdminSuspensionInput): Promise<Result<AdminOrgSummary>>;
  adminSetEntitlements(session: SessionContext, input: AdminEntitlementsInput): Promise<Result<OrgEntitlements>>;

  /**
   * Operator-only audit trail (R34): every `adminGrant`, `adminSetSuspension`,
   * `adminSetEntitlements` and `calibration.label` appends an immutable entry. Append-only, so a
   * restore never erases the suspension that preceded it.
   */
  adminAudit(session: SessionContext, query: AuditQuery): Promise<Result<Page<AuditEntry>>>;

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
  "calibration.label",
  "calibration.list",
  "settings.get",
  "settings.update",
  "keys.list",
  "keys.create",
  "keys.revoke",
  "adminOrgs",
  "adminGrant",
  "adminSetSuspension",
  "adminSetEntitlements",
  "adminAudit",
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
  "adminSetSuspension",
  "adminSetEntitlements",
  "adminAudit",
  "calibration.label",
  "calibration.list",
] as const satisfies readonly ConsoleOperation[];

/** Readable by the organization owner or a platform operator, never a member (R13). */
export const OWNER_OR_OPERATOR_OPERATIONS = ["judgeRuns"] as const satisfies readonly ConsoleOperation[];

/**
 * R33: what a suspended organization may still do. Every read stays available, and so does
 * `keys.revoke` — a leaked key must be revocable whatever the organization's status. Everything
 * that creates work or changes configuration returns `org_suspended`. Operator operations are not
 * listed: they act on the organization rather than for it, and must keep working (otherwise a
 * suspension could never be lifted).
 */
export const SUSPENDED_ALLOWED_OPERATIONS = [
  "usage",
  "usageSummary",
  "usageDaily",
  "balances",
  "ledger",
  "traces",
  "traceDetail",
  "traceContent",
  "feedback.list",
  "settings.get",
  "keys.list",
  "keys.revoke",
] as const satisfies readonly ConsoleOperation[];

/** Tenant-scoped operations a suspended organization is refused: new work or new configuration. */
export const SUSPENDED_REFUSED_OPERATIONS = [
  "keys.create",
  "settings.update",
  "feedback.submit",
  "judgeRuns",
] as const satisfies readonly ConsoleOperation[];

/** Operations whose only argument is a session, or a session plus an opaque identifier. */
export const OPERATIONS_WITHOUT_INPUT_OBJECT = [
  "balances",
  "traceDetail",
  "traceContent",
  "feedback.list",
  "settings.get",
  "keys.list",
  "keys.revoke",
] as const satisfies readonly ConsoleOperation[];

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
  "adminSetSuspension",
  "adminSetEntitlements",
  "calibration.label",
] as const satisfies readonly ConsoleOperation[];
