/**
 * Console contract vocabulary and DTOs — contracts v1 (research/plan/01-contracts.md)
 * as encoded by research/plan/08-contracts-v1-encoding.md §3 and §9.
 *
 * Every union is declared as a frozen `as const` list and the type derived from it, so
 * runtime guards and the compiler share one source (no `enum`: Node strips types only).
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import type { Money } from "./money.ts";

// ---------------------------------------------------------------------------
// Vocabulary — string values are frozen by 08 §3.
// ---------------------------------------------------------------------------

export const JOB_STATES = [
  "preparing",
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "expired",
] as const;
export type JobState = (typeof JOB_STATES)[number];

export const TERMINAL_JOB_STATES = ["succeeded", "failed", "cancelled", "expired"] as const;

export const EXECUTION_MODES = ["sync", "stream", "async"] as const;
export type ExecutionMode = (typeof EXECUTION_MODES)[number];

export const TERMINAL_CAUSES = [
  "completed",
  "client_cancelled",
  "client_disconnected",
  "sync_deadline",
  "queue_wait_expired",
  "deadline_exceeded",
  "invalid_media",
  "preparation_failed",
  "engine_error",
  "engine_incomplete",
  "lost_after_publication",
  "journal_write_failed",
  "retries_exhausted",
  "platform_error",
] as const;
export type TerminalCause = (typeof TERMINAL_CAUSES)[number];

export const USAGE_CERTAINTIES = ["authoritative", "unknown"] as const;
export type UsageCertainty = (typeof USAGE_CERTAINTIES)[number];

export const SETTLEMENT_STATES = [
  "settled",
  "released_free",
  "held_unknown",
  "released_platform_absorbed",
] as const;
export type SettlementState = (typeof SETTLEMENT_STATES)[number];

export const TRACE_MODES = ["off", "minimal", "full"] as const;
export type TraceMode = (typeof TRACE_MODES)[number];

export const TRACE_LOSS_REASONS = [
  "none",
  "memory_budget",
  "metadata_budget",
  "queue_full",
  "disk_budget",
  "disk_error",
  "shutdown",
  "malformed",
  "abandoned",
] as const;
export type TraceLossReason = (typeof TRACE_LOSS_REASONS)[number];

/** Hard bound on `Idempotency-Key` (08 §3); a longer key is `invalid_request`. */
export const MAX_IDEMPOTENCY_KEY_CHARS = 255;

export const FEEDBACK_CHANNELS = ["api", "console"] as const;
export type FeedbackChannel = (typeof FEEDBACK_CHANNELS)[number];

export const AUTHOR_ROLES = ["customer", "operator", "judge"] as const;
export type AuthorRole = (typeof AUTHOR_ROLES)[number];

export const JUDGE_RUN_STATES = [
  "dry_run",
  "reserved",
  "submitting",
  "submitted",
  "ambiguous",
  "collecting",
  "settled",
  "quarantined",
  "cancelled",
] as const;
export type JudgeRunState = (typeof JUDGE_RUN_STATES)[number];

export const ROLES = ["owner", "member", "operator", "service"] as const;
export type Role = (typeof ROLES)[number];

/** The organization roles a console session can carry; operator authority is a separate flag. */
export const ORG_ROLES = ["owner", "member"] as const;
export type OrgRole = (typeof ORG_ROLES)[number];

/** Trace content availability — the closed set of 08 §9. */
export const TRACE_CONTENT_AVAILABILITY = [
  "available",
  "metadata_only",
  "pending",
  "lost",
  "expired",
  "off",
] as const;
export type TraceContentAvailability = (typeof TRACE_CONTENT_AVAILABILITY)[number];

/** Error codes of 08 §3: HTTP table, in-stream terminal events, then internal domain errors. */
export const ERROR_CODES = [
  "invalid_request",
  "unsupported_parameter",
  "unsupported_media",
  "media_fetch_failed",
  "context_length_exceeded",
  "invalid_cursor",
  "invalid_api_key",
  "insufficient_credit",
  "forbidden",
  "org_suspended",
  "model_not_entitled",
  "not_found",
  "idempotency_conflict",
  "result_pending",
  "state_conflict",
  "result_expired",
  "upload_expired",
  "journal_expired",
  "replay_gap",
  "idempotency_expired",
  "request_too_large",
  "capacity_exhausted",
  "journal_capacity_exhausted",
  "rate_limited",
  "internal_error",
  "dependency_unavailable",
  "deadline_exceeded",
  "stream_interrupted",
  "status_unknown",
  "stale_lease",
  "already_terminal",
  "not_claimable",
  "capacity_unavailable",
  "budget_exceeded",
  "consent_missing",
  "ambiguous_submission",
  "journal_write_failed",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

/**
 * The three sets 08 §3 partitions the codes into, exported so the G0 parity test can compare them
 * against the Python `error_codes.json` set by set rather than code by code. `journal_write_failed`
 * is both a `TerminalCause` and an internal-only error (`StreamStore.append` raises it for an event
 * over `JOURNAL_EVENT_MAX_BYTES`); it never carries an HTTP status (R25).
 */
export const IN_STREAM_ONLY_CODES = ["stream_interrupted", "status_unknown"] as const;

export const INTERNAL_ONLY_CODES = [
  "already_terminal",
  "ambiguous_submission",
  "budget_exceeded",
  "capacity_unavailable",
  "consent_missing",
  "journal_write_failed",
  "not_claimable",
  "stale_lease",
] as const;

/**
 * The HTTP status each code is served with, frozen by the 08 §3 envelope table. `null` marks the
 * codes that never carry an HTTP status: the two in-stream terminal error events and the internal
 * domain errors. A trace row pairing a status with a code from another row is a bug, so the
 * conformance suite asserts the pairing instead of leaving each renderer to guess.
 */
export const ERROR_CODE_HTTP_STATUS: Readonly<Record<ErrorCode, number | null>> = {
  invalid_request: 400,
  unsupported_parameter: 400,
  unsupported_media: 400,
  media_fetch_failed: 400,
  context_length_exceeded: 400,
  invalid_cursor: 400,
  invalid_api_key: 401,
  insufficient_credit: 402,
  forbidden: 403,
  org_suspended: 403,
  model_not_entitled: 403,
  not_found: 404,
  idempotency_conflict: 409,
  result_pending: 409,
  state_conflict: 409,
  result_expired: 410,
  upload_expired: 410,
  journal_expired: 410,
  replay_gap: 410,
  idempotency_expired: 410,
  request_too_large: 413,
  capacity_exhausted: 429,
  journal_capacity_exhausted: 429,
  rate_limited: 429,
  internal_error: 500,
  dependency_unavailable: 503,
  deadline_exceeded: 504,
  stream_interrupted: null,
  status_unknown: null,
  stale_lease: null,
  already_terminal: null,
  not_claimable: null,
  capacity_unavailable: null,
  budget_exceeded: null,
  consent_missing: null,
  ambiguous_submission: null,
  journal_write_failed: null,
};

// ---------------------------------------------------------------------------
// Session, results and pagination
// ---------------------------------------------------------------------------

/**
 * The trusted caller identity. Resolved server-side from the session cookie;
 * never reconstructed from route, form or query input. `orgId` is the only tenant
 * binding a console service may use.
 */
export type SessionContext = {
  userId: string;
  email: string;
  orgId: string;
  orgName: string;
  role: OrgRole;
  isOperator: boolean;
};

export type ServiceError = {
  code: ErrorCode;
  message: string;
  request_id?: string;
};

export type Result<T, E = ServiceError> = { ok: true; value: T } | { ok: false; error: E };

/** Hard bound of 08 §9; a larger requested limit is `invalid_request`, not a clamp. */
export const MAX_PAGE_LIMIT = 100;
export const DEFAULT_PAGE_LIMIT = 25;

/** `next_cursor` is opaque: minted by the service, never constructed by a caller. */
export type Page<T> = {
  items: T[];
  next_cursor: string | null;
};

export type PageQuery = {
  limit?: number;
  cursor?: string | null;
};

export type UsageQuery = PageQuery & {
  from?: string;
  to?: string;
  key_id?: string;
  model?: string;
};

/**
 * The window an *aggregate* requires. `usageSummary` and `usageDaily` collapse rows into one
 * figure, and an aggregate with no bounds is a full-table scan whose answer nobody can check: it
 * silently mixes the legacy regime with the pilot one and grows without limit as history does. The
 * list keeps its optional window — a page is bounded by its limit — but the two aggregates require
 * `from` and `to`, and a caller that omits either is `invalid_request`, never an implicit "all time".
 */
export type UsageWindowQuery = UsageQuery & { from: string; to: string };

export type TraceQuery = PageQuery & {
  from?: string;
  to?: string;
  key_id?: string;
  model?: string;
  job_state?: JobState;
  trace_mode?: TraceMode;
  content?: TraceContentAvailability;
  has_feedback?: boolean;
};

/**
 * The accepted field names per input, so "extra fields are refused" is executable rather than a
 * comment. The Python half gets this from pydantic's `extra="forbid"` (08 §2); a console service
 * has to refuse the same way, or a caller that smuggles `org_id`, `author_role`, `channel` or a
 * storage key gets a silent no-op instead of a 400 and learns nothing.
 */
export const PAGE_QUERY_FIELDS = ["limit", "cursor"] as const;
export const USAGE_QUERY_FIELDS = [...PAGE_QUERY_FIELDS, "from", "to", "key_id", "model"] as const;
export const TRACE_QUERY_FIELDS = [
  ...USAGE_QUERY_FIELDS,
  "job_state",
  "trace_mode",
  "content",
  "has_feedback",
] as const;

// ---------------------------------------------------------------------------
// Usage and money
// ---------------------------------------------------------------------------

/**
 * Which accounting rules a usage row was written under.
 *
 * - `pilot` — settled by the metering path: every column below is populated and the settlement
 *   rules of R13 apply to it.
 * - `legacy_usd` — history from before the pilot regime. The row is a real charge in US dollars
 *   and must be *displayed and totalled*, but it was never a job: it has no execution mode, no job
 *   state, no usage certainty and no capture mode, and nothing may replay it into a debit.
 *
 * The regime is an explicit field rather than something inferred from which columns are null,
 * because "every pilot column happens to be null" is also what a broken projection looks like. It
 * is the console name for D1's `usage_events.settlement_regime` (`legacy` / `pilot`).
 */
export const ACCOUNTING_REGIMES = ["legacy_usd", "pilot"] as const;
export type AccountingRegime = (typeof ACCOUNTING_REGIMES)[number];

export type UsageRow = {
  request_id: string;
  created_at: string;
  model: string;
  /**
   * The key that made the request, or null for both fields together when that key has since been
   * deleted (D1 sets `usage_events.api_key_id` to null on delete, so the join yields both or
   * neither). The row is still the organization's traffic and is still counted: dropping it would
   * understate a cost total, and inventing a placeholder identifier would make it filterable.
   */
  key_id: string | null;
  key_name: string | null;
  /** Which rules wrote this row. Legacy rows keep their NULLs; nothing is invented for them. */
  accounting_regime: AccountingRegime;
  /** Null on a `legacy_usd` row only; a pilot row always carries all four. */
  execution_mode: ExecutionMode | null;
  job_state: JobState | null;
  terminal_cause: TerminalCause | null;
  http_status: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  usage_certainty: UsageCertainty | null;
  /** Null until the request reaches a terminal state: nothing has been settled yet (R13). */
  settlement_state: SettlementState | null;
  /** Charged amount; zero while unsettled, free or platform-absorbed. */
  cost: Money;
  /** Outstanding reservation, null once released. Never an authoritative charge. */
  max_hold: Money | null;
  trace_mode: TraceMode | null;
};

export type UsageSummary = {
  requests: number;
  failed_requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost: Money;
  /** Held reservations for unknown usage: awaiting reconciliation, not charged. */
  pending_reconciliation: Money;
  platform_absorbed_requests: number;
};

export type UsageDay = {
  day: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost: Money;
};

export type WalletBalance = {
  ledger_total: Money;
  reserved_total: Money;
  available: Money;
};

/**
 * Every kind a ledger row can *render* as. `purchase` is legacy and read-only (R13): historical
 * rows must display, and nothing in the free pilot creates one, because payments are out of scope
 * (DEC-01). A service that mints a `purchase` entry is a defect.
 */
export const LEDGER_ENTRY_KINDS = ["grant", "usage", "adjustment", "purchase"] as const;
export type LedgerEntryKind = (typeof LEDGER_ENTRY_KINDS)[number];

/** The kinds a running system may create; `purchase` is deliberately absent. */
export const CREATABLE_LEDGER_ENTRY_KINDS = ["grant", "usage", "adjustment"] as const;

/**
 * What a customer session sees in place of an operator's identity (R41). A tenant learns that the
 * platform acted, never which person at the platform did it; the real principal lives in the
 * operator-only audit trail.
 */
export const PLATFORM_ACTOR = "platform";

export type LedgerEntry = {
  id: string;
  created_at: string;
  /** Grants positive, debits negative; corrections are new entries, never edits. */
  delta: Money;
  kind: LedgerEntryKind;
  reason: string | null;
  ref: string | null;
  /**
   * Who caused the entry, as *this* session may know it: the organization's own principal for its
   * own actions, the literal `platform` for anything an operator did, and null where nobody did
   * (a usage debit). An operator principal never appears here (R41).
   */
  actor: string | null;
};

// ---------------------------------------------------------------------------
// API keys
// ---------------------------------------------------------------------------

export type ApiKeySummary = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  /**
   * Null is **off**, not "unknown" and not a default to be filled in (`api_keys.trace_mode` is a
   * nullable column added by D1, so every key that predates it reads null). Capture is consent,
   * so the absence of a recorded choice can only mean no capture; a reader that treats null as
   * anything else would start capturing content nobody opted into. Use `traceModeOf`.
   */
  trace_mode: TraceMode | null;
};

/** The one reading of a null capture mode: off. A reader that spells this differently is a defect. */
export function traceModeOf(key: Pick<ApiKeySummary, "trace_mode">): TraceMode {
  return key.trace_mode ?? "off";
}

/** A key label is a label: bounded so a form cannot post a document into the keys page (R17). */
export const MAX_KEY_NAME_CHARS = 200;

export type ApiKeyCreateInput = {
  name: string;
  trace_mode?: TraceMode;
  /** Optional; supplied, a retried creation replays the first key instead of minting a second. */
  idempotency_key?: string;
};

export const API_KEY_CREATE_FIELDS = ["name", "trace_mode", "idempotency_key"] as const;

/**
 * The secret exists only in the *first* response (create-once presentation, R16). A replay under
 * the same idempotency key returns the key's current metadata — including a `revoked_at` set since
 * — with `secret: null` and `replayed: true`, for every session including the one that created it.
 * The secret is never part of any stored state, so no authorization can make a replay produce it.
 */
export type ApiKeyCreated = ApiKeySummary & { secret: string | null; replayed: boolean };

// ---------------------------------------------------------------------------
// Traces
// ---------------------------------------------------------------------------

export type TraceListItem = {
  request_id: string;
  created_at: string;
  model: string;
  key_id: string;
  job_state: JobState;
  http_status: number;
  /** Capture mode in force for the request; `off` rows are not trace-coverage denominators. */
  trace_mode: TraceMode;
  content: TraceContentAvailability;
  loss_reason: TraceLossReason;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  ttft_ms: number | null;
  wall_ms: number;
  cost: Money;
  feedback_count: number;
  score_count: number;
};

export type TraceTimings = {
  auth_ms: number | null;
  media_ms: number | null;
  admit_ms: number | null;
  queue_ms: number | null;
  ttft_ms: number | null;
  wall_ms: number;
};

export type TraceVersions = {
  gateway_version: string;
  model_revision: string;
  price_snapshot_version: string;
  trace_schema_version: number;
};

export type TraceDetail = TraceListItem & {
  execution_mode: ExecutionMode;
  terminal_cause: TerminalCause | null;
  error_code: ErrorCode | null;
  usage_certainty: UsageCertainty;
  /** Null until terminal, exactly as on the usage row it projects (R13). */
  settlement_state: SettlementState | null;
  timings: TraceTimings;
  versions: TraceVersions;
  /** Logical expiry of full content (owner-selected, <= 90 days); null when there is none. */
  content_expires_at: string | null;
  /** Metadata retention horizon (13 calendar months). */
  metadata_expires_at: string;
  feedback: FeedbackEntry[];
};

export type TracePart =
  | { type: "text"; text: string }
  | { type: "video_url"; video_url: { url: string; source: "url" | "data" | "upload" } };

export type TraceMessage = { role: string; content: string | TracePart[] };

export type TraceChoice = {
  index: number;
  finish_reason: string | null;
  content: string | null;
  reasoning_content: string | null;
};

export type TraceContentBody = {
  v: 1;
  request: {
    model: string;
    messages: TraceMessage[];
    params: Record<string, unknown>;
  };
  response: {
    status: number;
    choices: TraceChoice[];
    error: { type: string; code: ErrorCode; message: string } | null;
  };
};

/**
 * Content is resolved server-side after ownership and logical expiry.
 * No storage key or signed URL is ever part of this DTO.
 */
export type TraceContentView = {
  request_id: string;
  availability: TraceContentAvailability;
  expires_at: string | null;
  content: TraceContentBody | null;
};

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

/**
 * The feedback body of R3, following `research/traces/06` §2: one named signal per submission.
 * `thumb` carries a boolean, `rating` an integer 1–5, `correction` and `comment` non-empty text.
 */
export const FEEDBACK_NAMES = ["thumb", "rating", "correction", "comment"] as const;
export type FeedbackName = (typeof FEEDBACK_NAMES)[number];

/**
 * Every name a *stored* entry may carry: the four a client may submit plus the operator
 * calibration label, which only `calibration.label` creates (R19). Keeping the two lists separate
 * is what makes "a client cannot author an operator label" a type-level fact rather than a check.
 */
export const FEEDBACK_ENTRY_NAMES = [...FEEDBACK_NAMES, "calibration_label"] as const;
export type FeedbackEntryName = (typeof FEEDBACK_ENTRY_NAMES)[number];

export const FEEDBACK_RATING_MIN = 1;
export const FEEDBACK_RATING_MAX = 5;

export type FeedbackValue = boolean | number | string;

/**
 * What a client may submit. Channel, author role, principal and calibration membership are
 * server-set and have no field here; the idempotency key is required (R3), so a retried submit
 * replays the original acceptance instead of recording the signal twice.
 */
export type FeedbackInput = {
  request_id: string;
  name: FeedbackName;
  value: FeedbackValue;
  comment?: string | null;
  idempotency_key: string;
};

export type FeedbackEntry = {
  id: string;
  request_id: string;
  created_at: string;
  channel: FeedbackChannel;
  author_role: AuthorRole;
  author_principal: string;
  name: FeedbackEntryName;
  value: FeedbackValue;
  comment: string | null;
  /** Calibration membership requires explicit platform-operator authorization. */
  calibration_set: boolean;
  /**
   * The rubric an operator labelled against. Set only on a `calibration_label` entry; null on
   * every customer signal, whatever session submitted it (R19).
   */
  rubric_version: number | null;
};

export const FEEDBACK_INPUT_FIELDS = [
  "request_id",
  "name",
  "value",
  "comment",
  "idempotency_key",
] as const;

/** Free-text bound on submitted feedback: a comment is a note, not an upload channel. */
export const MAX_FEEDBACK_TEXT_CHARS = 4000;

// ---------------------------------------------------------------------------
// Operator calibration (R19)
// ---------------------------------------------------------------------------

/**
 * The verdict an operator records against a rubric. This — and only this — produces an entry with
 * `author_role: "operator"` and `calibration_set: true`; ordinary console feedback from an
 * operator session stays a customer signal (02: "console-origin input is not automatically an
 * operator label").
 */
export const CALIBRATION_LABELS = ["correct", "partially_correct", "incorrect", "unusable"] as const;
export type CalibrationLabelValue = (typeof CALIBRATION_LABELS)[number];

export type CalibrationLabelInput = {
  request_id: string;
  rubric_version: number;
  label: CalibrationLabelValue;
  comment?: string | null;
  idempotency_key: string;
};

export const CALIBRATION_LABEL_FIELDS = [
  "request_id",
  "rubric_version",
  "label",
  "comment",
  "idempotency_key",
] as const;

/** Rubric versions are small positive integers; a provisional bound, like the other two (R17). */
export const MAX_RUBRIC_VERSION = 1000;

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

/** DEC-05 cap on owner-selected full-content retention. */
export const MAX_CONTENT_RETENTION_DAYS = 90;

export type ConsentHistoryEntry = {
  changed_at: string;
  evaluation_consent: boolean;
  /** The organization's own principal, or `platform` when an operator made the change (R41). */
  changed_by: string;
};

export type ConsoleSettings = {
  trace_mode: TraceMode;
  content_retention_days: number;
  /** Independent of trace mode (DEC-10); must be current at judge submission. */
  evaluation_consent: boolean;
  consent_history: ConsentHistoryEntry[];
};

export type SettingsUpdate = {
  trace_mode?: TraceMode;
  content_retention_days?: number;
  evaluation_consent?: boolean;
  /** Optional; supplied, a retried update does not append a second consent-history entry. */
  idempotency_key?: string;
};

export const SETTINGS_UPDATE_FIELDS = [
  "trace_mode",
  "content_retention_days",
  "evaluation_consent",
  "idempotency_key",
] as const;

// ---------------------------------------------------------------------------
// Operator administration
// ---------------------------------------------------------------------------

/**
 * What an organization is entitled to run. The limit names are a closed provisional set: an
 * unknown name is `invalid_request` rather than a silently ignored control (R17/R19).
 */
export const ENTITLEMENT_LIMIT_NAMES = [
  "max_concurrent_requests",
  "max_requests_per_minute",
  "max_video_seconds",
] as const;
export type EntitlementLimitName = (typeof ENTITLEMENT_LIMIT_NAMES)[number];

/** Provisional ceiling on any single entitlement limit, so a typo cannot mean "unlimited". */
export const MAX_ENTITLEMENT_LIMIT = 1000000;

/**
 * What an organization may call (R24), stated explicitly so the default cannot be confused with a
 * denial:
 *
 * - `null` — the platform default set. No per-organization decision has been recorded.
 * - `[]` — **nothing entitled**. Every admission fails `model_not_entitled`. This is a deliberate
 *   fail-closed state, not an empty field.
 * - a non-empty list — exactly those models, and nothing else.
 */
export type OrgEntitlements = {
  org_id: string;
  model_ids: string[] | null;
  limits: Partial<Record<EntitlementLimitName, number>>;
  updated_at: string | null;
  updated_by: string | null;
};

export type AdminOrgSummary = {
  org_id: string;
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  /** Why the organization is suspended, or why it was last unsuspended; null if never set. */
  suspension_reason: string | null;
  balance: WalletBalance;
  requests_30d: number;
  entitlements: OrgEntitlements;
};

export type AdminSuspensionInput = {
  target_org_id: string;
  suspended: boolean;
  reason: string;
  idempotency_key: string;
};

export const ADMIN_SUSPENSION_FIELDS = [
  "target_org_id",
  "suspended",
  "reason",
  "idempotency_key",
] as const;

// ---------------------------------------------------------------------------
// Operator audit trail (R34)
// ---------------------------------------------------------------------------

/** Every operator write names itself. The list is closed: a new operator write adds a value here. */
export const AUDIT_ACTIONS = [
  "grant",
  "suspension_set",
  "entitlements_set",
  "calibration_label",
] as const;
export type AuditAction = (typeof AUDIT_ACTIONS)[number];

/**
 * An immutable record of one operator write. Append-only: a restore *adds* an entry rather than
 * overwriting the suspension that preceded it, so the history of a tenant's status survives
 * whatever its current state says.
 */
export type AuditEntry = {
  id: string;
  at: string;
  actor_principal: string;
  action: AuditAction;
  /**
   * The organization the write landed on, or null once that organization has been deleted: the
   * trail is append-only and outlives its targets (`on delete set null` in D1), so an entry whose
   * target is gone is still the record that the write happened. It is never back-filled.
   */
  target_org_id: string | null;
  reason: string;
  /** The fields this write changed, before and after. `null` before a creation. */
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
  idempotency_key: string;
};

export type AuditQuery = PageQuery & { target_org_id?: string };

export const AUDIT_QUERY_FIELDS = [...PAGE_QUERY_FIELDS, "target_org_id"] as const;

export type AdminEntitlementsInput = {
  target_org_id: string;
  /** `null` restores the platform default; `[]` entitles nothing; a list is exactly that set. */
  model_ids: string[] | null;
  limits: Partial<Record<EntitlementLimitName, number>>;
  reason: string;
  idempotency_key: string;
};

export const ADMIN_ENTITLEMENTS_FIELDS = [
  "target_org_id",
  "model_ids",
  "limits",
  "reason",
  "idempotency_key",
] as const;

/** The only allowed grant kind in the free pilot (DEC-01). */
export const GRANT_KINDS = ["promotional"] as const;
export type GrantKind = (typeof GRANT_KINDS)[number];

export type AdminGrantInput = {
  /** Operator-chosen target organization, authorized against the operator flag. */
  target_org_id: string;
  amount: Money;
  kind: GrantKind;
  reason: string;
  idempotency_key: string;
};

export const ADMIN_GRANT_FIELDS = [
  "target_org_id",
  "amount",
  "kind",
  "reason",
  "idempotency_key",
] as const;

/** A grant needs a reason a human wrote, not an essay. */
export const MAX_GRANT_REASON_CHARS = 500;

export type AdminGrantResult = {
  grant_id: string;
  org_id: string;
  amount: Money;
  kind: GrantKind;
  reason: string;
  created_at: string;
  operator_principal: string;
  /** True when the same key and payload returned the original grant. */
  replayed: boolean;
  balance: WalletBalance;
};

// ---------------------------------------------------------------------------
// Judge
// ---------------------------------------------------------------------------

export const JUDGE_MODES = ["dry_run", "live"] as const;
export type JudgeMode = (typeof JUDGE_MODES)[number];

export const JUDGE_SCORE_KINDS = ["numeric", "boolean", "label", "text"] as const;
export type JudgeScoreKind = (typeof JUDGE_SCORE_KINDS)[number];

export const JUDGE_LIMITED_REASONS = ["no_media", "content_expired"] as const;
export type JudgeLimitedReason = (typeof JUDGE_LIMITED_REASONS)[number];

export type JudgeScore = {
  name: string;
  kind: JudgeScoreKind;
  value_num: number | null;
  value_bool: boolean | null;
  value_label: string | null;
  value_text: string | null;
  rationale: string | null;
  rubric_version: number;
  judge_model: string;
  /** Null until the provider names the revision it served; a dry run has no provider. */
  judge_model_version: string | null;
  /** Dry-run output: an estimate, never a provider result. */
  estimated: boolean;
};

/**
 * One evaluated sample, frozen to the shape D1's `console_judge_runs` view emits and nothing else
 * (previously the console carried a second, richer shape that no query could produce).
 *
 * - `sample_id` is the sample's own identifier, a lower-case UUIDv4, unique within its run;
 * - `rubric_version` is the sample's, not the run's: late results deduplicate by
 *   (run, sample, rubric version), so one run may carry samples from more than one version;
 * - `request_id` is null when the trace the sample scored has since been deleted;
 * - `scores` is empty when nothing has been collected yet.
 *
 * `limited_evaluation`/`limited_reason` are deliberately absent: the run carries
 * `limited_evaluation_count`, which is what the console renders, and a per-sample flag no view
 * selects would have been a field only the fake could fill.
 */
export type JudgeSample = {
  sample_id: string;
  rubric_version: number;
  request_id: string | null;
  scores: JudgeScore[];
};

/**
 * The most samples one run row carries. D1 caps the embedded array so a run with ten thousand
 * samples cannot turn one page of a list into a multi-megabyte document, which is why
 * `sample_count` is the run's own count and is **not** `samples.length`.
 */
export const JUDGE_RUN_SAMPLE_CAP = 50;

export type JudgeRun = {
  id: string;
  created_at: string;
  state: JudgeRunState;
  mode: JudgeMode;
  rubric_version: number;
  judge_model: string;
  /** Null on a run that never reached a provider: a dry run does not learn a served revision. */
  judge_model_version: string | null;
  /** Every sample of the run, which is not `samples.length` once the cap bites. */
  sample_count: number;
  limited_evaluation_count: number;
  /** Worst-case reservation; stays held while a submission outcome is ambiguous. */
  budget_reserved: Money;
  budget_settled: Money | null;
  /**
   * When the consent this run was authorized by took effect, or null if that consent-history row
   * has since been revoked and removed: the run is still a fact, and a missing snapshot instant is
   * not an excuse to invent one (D1 reaches it through an outer join).
   */
  consent_snapshot_at: string | null;
  external_batch_id: string | null;
  quarantine_reason: string | null;
  samples: JudgeSample[];
};
