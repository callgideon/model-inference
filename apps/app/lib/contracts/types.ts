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
] as const;
export type TraceLossReason = (typeof TRACE_LOSS_REASONS)[number];

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
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

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

// ---------------------------------------------------------------------------
// Usage and money
// ---------------------------------------------------------------------------

export type UsageRow = {
  request_id: string;
  created_at: string;
  model: string;
  key_id: string;
  key_name: string;
  execution_mode: ExecutionMode;
  job_state: JobState;
  terminal_cause: TerminalCause | null;
  http_status: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  usage_certainty: UsageCertainty;
  settlement_state: SettlementState;
  /** Charged amount; zero while unsettled, free or platform-absorbed. */
  cost: Money;
  /** Outstanding reservation, null once released. Never an authoritative charge. */
  max_hold: Money | null;
  trace_mode: TraceMode;
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

/** Free pilot kinds only; `purchase` stays deferred with payments (DEC-01). */
export const LEDGER_ENTRY_KINDS = ["grant", "usage", "adjustment"] as const;
export type LedgerEntryKind = (typeof LEDGER_ENTRY_KINDS)[number];

export type LedgerEntry = {
  id: string;
  created_at: string;
  /** Grants positive, debits negative; corrections are new entries, never edits. */
  delta: Money;
  kind: LedgerEntryKind;
  reason: string | null;
  ref: string | null;
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
  trace_mode: TraceMode;
};

export type ApiKeyCreateInput = {
  name: string;
  trace_mode?: TraceMode;
};

/** The secret exists only in this response (create-once presentation). */
export type ApiKeyCreated = ApiKeySummary & { secret: string };

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
  settlement_state: SettlementState;
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

export const FEEDBACK_RATINGS = ["up", "down"] as const;
export type FeedbackRating = (typeof FEEDBACK_RATINGS)[number];

/** What a client may submit. Channel, author role and calibration are server-set. */
export type FeedbackInput = {
  request_id: string;
  rating: FeedbackRating;
  comment?: string | null;
  correction?: string | null;
};

export type FeedbackEntry = {
  id: string;
  request_id: string;
  created_at: string;
  channel: FeedbackChannel;
  author_role: AuthorRole;
  author_principal: string;
  rating: FeedbackRating;
  comment: string | null;
  correction: string | null;
  /** Calibration membership requires explicit platform-operator authorization. */
  calibration_set: boolean;
};

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

/** DEC-05 cap on owner-selected full-content retention. */
export const MAX_CONTENT_RETENTION_DAYS = 90;

export type ConsentHistoryEntry = {
  changed_at: string;
  evaluation_consent: boolean;
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
};

// ---------------------------------------------------------------------------
// Operator administration
// ---------------------------------------------------------------------------

export type AdminOrgSummary = {
  org_id: string;
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  balance: WalletBalance;
  requests_30d: number;
};

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
  judge_model_version: string;
  /** Dry-run output: an estimate, never a provider result. */
  estimated: boolean;
};

export type JudgeSample = {
  id: string;
  run_id: string;
  request_id: string;
  /** No media means no groundedness pass; the scores are explicitly partial. */
  limited_evaluation: boolean;
  limited_reason: JudgeLimitedReason | null;
  scores: JudgeScore[];
};

export type JudgeRun = {
  id: string;
  created_at: string;
  state: JudgeRunState;
  mode: JudgeMode;
  rubric_version: number;
  judge_model: string;
  judge_model_version: string;
  sample_count: number;
  limited_evaluation_count: number;
  /** Worst-case reservation; stays held while a submission outcome is ambiguous. */
  budget_reserved: Money;
  budget_settled: Money | null;
  consent_snapshot_at: string;
  external_batch_id: string | null;
  quarantine_reason: string | null;
  samples: JudgeSample[];
};
