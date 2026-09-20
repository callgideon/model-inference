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
  addMoney,
  isNegativeMoney,
  isZeroMoney,
  moneyFromUnits,
  moneyUnits,
  parseMoney,
  subMoney,
  sumMoney,
  ZERO_MONEY,
  type Money,
} from "./money.ts";
import {
  DEFAULT_PAGE_LIMIT,
  FEEDBACK_RATINGS,
  MAX_CONTENT_RETENTION_DAYS,
  MAX_PAGE_LIMIT,
  ORG_ROLES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_MODES,
  type AdminGrantResult,
  type AdminOrgSummary,
  type ApiKeyCreated,
  type ApiKeySummary,
  type ConsoleSettings,
  type ErrorCode,
  type ExecutionMode,
  type FeedbackEntry,
  type JobState,
  type JudgeRun,
  type JudgeSample,
  type JudgeScore,
  type LedgerEntry,
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
  usage_rows: number;
  all_free: boolean;
  grants: GrantFixture[];
  adjustment: GrantFixture | null;
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
  sessions: Record<"owner" | "member" | "operator" | "otherOwner", SessionFixture>;
};

const traceFixture = traceFixtureJson as unknown as {
  availability_cycle: string[];
  content_templates: TraceContentBody[];
  seed_feedback: {
    trace_index: number;
    channel: string;
    author_role: string;
    author_principal: string;
    rating: string;
    comment: string | null;
    correction: string | null;
    calibration_set: boolean;
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

function encodeCursor(offset: number, scope: string): string {
  return btoa(JSON.stringify({ o: offset, k: fnv1a(scope) }));
}

/** Any cursor this service did not mint for this exact query is rejected. */
function decodeCursor(cursor: string, scope: string): number | null {
  try {
    const parsed = JSON.parse(atob(cursor)) as { o?: unknown; k?: unknown };
    if (typeof parsed.o !== "number" || !Number.isInteger(parsed.o) || parsed.o < 0) return null;
    if (parsed.k !== fnv1a(scope)) return null;
    return parsed.o;
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

function paginate<T>(rows: T[], query: PageQuery, scope: string): Result<Page<T>> {
  const limit = query.limit ?? DEFAULT_PAGE_LIMIT;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
    return fail("invalid_request", `limit must be an integer between 1 and ${MAX_PAGE_LIMIT}`);
  }
  let offset = 0;
  if (query.cursor !== undefined && query.cursor !== null && query.cursor !== "") {
    const decoded = decodeCursor(query.cursor, scope);
    if (decoded === null) {
      return fail("invalid_cursor", "the cursor was not issued by this service for this query");
    }
    offset = decoded;
  }
  const items = structuredClone(rows.slice(offset, offset + limit));
  const nextOffset = offset + items.length;
  return ok({ items, next_cursor: nextOffset < rows.length ? encodeCursor(nextOffset, scope) : null });
}

// ---------------------------------------------------------------------------
// Generated organization state
// ---------------------------------------------------------------------------

type Outcome = {
  job_state: JobState;
  terminal_cause: TerminalCause | null;
  http_status: number;
  usage_certainty: UsageCertainty;
  settlement_state: SettlementState;
  billable: boolean;
  holds: boolean;
};

const SUCCEEDED: Outcome = {
  job_state: "succeeded",
  terminal_cause: "completed",
  http_status: 200,
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
          usage_certainty: "authoritative",
          settlement_state: "released_platform_absorbed",
          billable: false,
          holds: false,
        }
      : {
          job_state: "failed",
          terminal_cause: "invalid_media",
          http_status: 400,
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
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  settings: ConsoleSettings;
  keys: ApiKeySummary[];
  usage: UsageRow[];
  ledger: LedgerEntry[];
  traces: TraceDetail[];
  content: Map<string, TraceContentView>;
  judge: JudgeRun[];
  /** Grant idempotency: org + operation + key, with the canonical payload it accepted. */
  grants: Map<string, { payload: string; result: AdminGrantResult }>;
  counter: number;
};

const CLOCK_MS = Date.parse(orgsFixture.clock);
const MAX_OUTPUT_TOKENS = 2048;
const INPUT_UNITS = BigInt(orgsFixture.rate_units_per_token.input);
const OUTPUT_UNITS = BigInt(orgsFixture.rate_units_per_token.output);

function costOf(promptTokens: number, completionTokens: number): Money {
  return moneyFromUnits(BigInt(promptTokens) * INPUT_UNITS + BigInt(completionTokens) * OUTPUT_UNITS);
}

const EXECUTION_MODE_CYCLE: ExecutionMode[] = ["sync", "stream", "async"];

function availabilityFor(mode: TraceMode, index: number): TraceContentAvailability {
  if (mode === "off") return "off";
  if (mode === "minimal") return "metadata_only";
  return pick(
    TRACE_CONTENT_AVAILABILITY,
    traceFixture.availability_cycle[index % traceFixture.availability_cycle.length],
    "availability_cycle",
  );
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
  const content = new Map<string, TraceContentView>();
  const retentionMs = spec.settings.content_retention_days * 86400000;

  for (let i = 0; i < spec.usage_rows; i += 1) {
    const key = activeKeys[i % activeKeys.length];
    const model = orgsFixture.models[i % orgsFixture.models.length];
    const createdMs = CLOCK_MS - (i + 1) * 137000;
    const created_at = new Date(createdMs).toISOString();
    const promptTokens = 512 + Math.floor(random() * 20000);
    const completionTokens = 32 + Math.floor(random() * 480);
    const ttft = 180 + Math.floor(random() * 900);
    const wall = ttft + 400 + Math.floor(random() * 4000);
    const outcome = outcomeFor(i, spec.all_free);
    const known = outcome.usage_certainty === "authoritative";
    const request_id = deterministicUuid(spec.namespace, i + 1);

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
      settlement_state: outcome.settlement_state,
      cost: outcome.billable ? costOf(promptTokens, completionTokens) : ZERO_MONEY,
      max_hold: outcome.holds ? costOf(promptTokens, MAX_OUTPUT_TOKENS) : null,
      trace_mode: key.trace_mode,
    });

    const availability = availabilityFor(key.trace_mode, i);
    const expired = availability === "expired";
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
        : new Date(expired ? createdMs - 86400000 : createdMs + retentionMs).toISOString();

    traces.push({
      ...list,
      execution_mode: usage[i].execution_mode,
      terminal_cause: outcome.terminal_cause,
      error_code: outcome.http_status >= 400 ? (outcome.http_status === 400 ? "unsupported_media" : "internal_error") : null,
      usage_certainty: outcome.usage_certainty,
      settlement_state: outcome.settlement_state,
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
      metadata_expires_at: new Date(createdMs + 396 * 86400000).toISOString(),
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
  ledger.sort((a, b) => (a.created_at === b.created_at ? b.id.localeCompare(a.id) : b.created_at.localeCompare(a.created_at)));

  const org: OrgState = {
    org_id: spec.org_id,
    name: spec.name,
    owner_email: spec.owner_email,
    created_at: spec.created_at,
    suspended: spec.suspended,
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
    grants: new Map(),
    counter: 0,
  };

  // Seeded feedback and judge runs belong to the established organization only.
  if (spec.grants.length > 0) {
    traceFixture.seed_feedback.forEach((seed, index) => {
      const trace = org.traces[seed.trace_index];
      if (trace === undefined) return;
      trace.feedback.push({
        id: `fb_${hex(spec.namespace * 17 + index, 12)}`,
        request_id: trace.request_id,
        created_at: seed.created_at,
        channel: seed.channel === "api" ? "api" : "console",
        author_role: seed.author_role === "operator" ? "operator" : seed.author_role === "judge" ? "judge" : "customer",
        author_principal: seed.author_principal,
        rating: pick(FEEDBACK_RATINGS, seed.rating, "seed_feedback rating"),
        comment: seed.comment,
        correction: seed.correction,
        calibration_set: seed.calibration_set,
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
    org.judge.sort((a, b) => b.created_at.localeCompare(a.created_at));
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
};

export type FakeConsoleServices = ConsoleServices & {
  sessions: FakeSessions;
  ids: FakeIds;
  /** Deterministic failure injection: the next call of `operation` returns this error. */
  failNext(operation: ConsoleOperation, code: ErrorCode, message?: string): void;
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

  const injected = new Map<ConsoleOperation, { code: ErrorCode; message: string }[]>();
  let clockMs = CLOCK_MS;
  let counter = 0;

  function nextTimestamp(): string {
    clockMs += 1000;
    return new Date(clockMs).toISOString();
  }

  function intercept<T>(operation: ConsoleOperation): Result<T> | null {
    const queue = injected.get(operation);
    if (queue === undefined || queue.length === 0) return null;
    const next = queue.shift();
    if (next === undefined) return null;
    return fail<T>(next.code, next.message);
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

  function balanceOf(org: OrgState): WalletBalance {
    const ledger_total = sumMoney(org.ledger.map((entry) => entry.delta));
    const holds: Money[] = [];
    for (const row of org.usage) if (row.max_hold !== null) holds.push(row.max_hold);
    const reserved_total = sumMoney(holds);
    return { ledger_total, reserved_total, available: subMoney(ledger_total, reserved_total) };
  }

  function usageRowsFor(org: OrgState, query: UsageQuery): UsageRow[] {
    return org.usage.filter(
      (row) =>
        (query.from === undefined || row.created_at >= query.from) &&
        (query.to === undefined || row.created_at <= query.to) &&
        (query.key_id === undefined || row.key_id === query.key_id) &&
        (query.model === undefined || row.model === query.model),
    );
  }

  function traceRowsFor(org: OrgState, query: TraceQuery): TraceListItem[] {
    return org.traces.filter(
      (row) =>
        (query.from === undefined || row.created_at >= query.from) &&
        (query.to === undefined || row.created_at <= query.to) &&
        (query.key_id === undefined || row.key_id === query.key_id) &&
        (query.model === undefined || row.model === query.model) &&
        (query.job_state === undefined || row.job_state === query.job_state) &&
        (query.trace_mode === undefined || row.trace_mode === query.trace_mode) &&
        (query.content === undefined || row.content === query.content) &&
        (query.has_feedback === undefined || query.has_feedback === row.feedback_count > 0),
    );
  }

  function scopeOf(org: OrgState, operation: string, query: object): string {
    const parts = Object.entries(query)
      .filter(([name, value]) => name !== "cursor" && value !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return `${org.org_id}|${operation}|${JSON.stringify(parts)}`;
  }

  function ownedTrace(org: OrgState, requestId: string): TraceDetail | undefined {
    return org.traces.find((trace) => trace.request_id === requestId);
  }

  const services: FakeConsoleServices = {
    sessions: {
      owner: sessionOf(orgsFixture.sessions.owner),
      member: sessionOf(orgsFixture.sessions.member),
      operator: sessionOf(orgsFixture.sessions.operator),
      otherOwner: sessionOf(orgsFixture.sessions.otherOwner),
    },
    ids: (() => {
      const first = orgsFixture.orgs[0];
      const second = orgsFixture.orgs[1];
      const orgA = orgs.get(first.org_id);
      const orgB = orgs.get(second.org_id);
      if (orgA === undefined || orgB === undefined) throw new Error("fixture organizations missing");
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
      };
    })(),

    failNext(operation, code, message = "injected failure") {
      const queue = injected.get(operation) ?? [];
      queue.push({ code, message });
      injected.set(operation, queue);
    },

    async usage(session, query) {
      const injectedResult = intercept<Page<UsageRow>>("usage");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<Page<UsageRow>>(session);
      if (isError(resolved)) return resolved;
      const rows = usageRowsFor(resolved.org, query);
      return paginate(rows, query, scopeOf(resolved.org, "usage", query));
    },

    async usageSummary(session, query) {
      const injectedResult = intercept<UsageSummary>("usageSummary");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<UsageSummary>(session);
      if (isError(resolved)) return resolved;
      const rows = usageRowsFor(resolved.org, query);
      const holds: Money[] = [];
      let cost = ZERO_MONEY;
      let prompt = 0;
      let completion = 0;
      let failed = 0;
      let absorbed = 0;
      for (const row of rows) {
        cost = addMoney(cost, row.cost);
        prompt += row.prompt_tokens ?? 0;
        completion += row.completion_tokens ?? 0;
        if (row.http_status >= 400) failed += 1;
        if (row.settlement_state === "released_platform_absorbed") absorbed += 1;
        if (row.settlement_state === "held_unknown" && row.max_hold !== null) holds.push(row.max_hold);
      }
      return ok({
        requests: rows.length,
        failed_requests: failed,
        prompt_tokens: prompt,
        completion_tokens: completion,
        cost,
        pending_reconciliation: sumMoney(holds),
        platform_absorbed_requests: absorbed,
      });
    },

    async usageDaily(session, query) {
      const injectedResult = intercept<UsageDay[]>("usageDaily");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<UsageDay[]>(session);
      if (isError(resolved)) return resolved;
      const days = new Map<string, UsageDay>();
      for (const row of usageRowsFor(resolved.org, query)) {
        const day = row.created_at.slice(0, 10);
        const existing = days.get(day) ?? {
          day,
          requests: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          cost: ZERO_MONEY,
        };
        existing.requests += 1;
        existing.prompt_tokens += row.prompt_tokens ?? 0;
        existing.completion_tokens += row.completion_tokens ?? 0;
        existing.cost = addMoney(existing.cost, row.cost);
        days.set(day, existing);
      }
      return ok([...days.values()].sort((a, b) => b.day.localeCompare(a.day)));
    },

    async balances(session) {
      const injectedResult = intercept<WalletBalance>("balances");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<WalletBalance>(session);
      if (isError(resolved)) return resolved;
      return ok(balanceOf(resolved.org));
    },

    async ledger(session, query) {
      const injectedResult = intercept<Page<LedgerEntry>>("ledger");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<Page<LedgerEntry>>(session);
      if (isError(resolved)) return resolved;
      return paginate(resolved.org.ledger, query, scopeOf(resolved.org, "ledger", query));
    },

    async traces(session, query) {
      const injectedResult = intercept<Page<TraceListItem>>("traces");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<Page<TraceListItem>>(session);
      if (isError(resolved)) return resolved;
      const rows = traceRowsFor(resolved.org, query);
      return paginate(rows, query, scopeOf(resolved.org, "traces", query));
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
        const resolved = tenant<FeedbackEntry>(session);
        if (isError(resolved)) return resolved;
        if (!(FEEDBACK_RATINGS as readonly string[]).includes(input.rating)) {
          return fail("invalid_request", "rating must be up or down");
        }
        const trace = ownedTrace(resolved.org, input.request_id);
        if (trace === undefined) return fail("not_found", "no such request for this organization");
        counter += 1;
        // Provenance is server-set: the channel is this console, the role comes from the
        // authenticated principal, and calibration membership needs a separate operator action.
        const entry: FeedbackEntry = {
          id: `fb_${hex(counter, 12)}`,
          request_id: trace.request_id,
          created_at: nextTimestamp(),
          channel: "console",
          author_role: session.isOperator ? "operator" : "customer",
          author_principal: session.email,
          rating: input.rating,
          comment: input.comment ?? null,
          correction: input.correction ?? null,
          calibration_set: false,
        };
        trace.feedback.push(entry);
        trace.feedback_count = trace.feedback.length;
        return ok(structuredClone(entry));
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
        const denied = requireOwner<ConsoleSettings>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ConsoleSettings>(session);
        if (isError(resolved)) return resolved;
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
        return ok(structuredClone(settings));
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
        const denied = requireOwner<ApiKeyCreated>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ApiKeyCreated>(session);
        if (isError(resolved)) return resolved;
        if (typeof input.name !== "string" || input.name.trim() === "") {
          return fail("invalid_request", "a key needs a name");
        }
        if (input.trace_mode !== undefined && !(TRACE_MODES as readonly string[]).includes(input.trace_mode)) {
          return fail("invalid_request", "trace_mode must be off, minimal or full");
        }
        resolved.org.counter += 1;
        const suffix = hex(resolved.org.counter * 7919, 8);
        const summary: ApiKeySummary = {
          id: deterministicUuid(4242, resolved.org.counter),
          name: input.name.trim(),
          prefix: `sk-infrx-${suffix}`,
          created_at: nextTimestamp(),
          last_used_at: null,
          revoked_at: null,
          trace_mode: input.trace_mode ?? resolved.org.settings.trace_mode,
        };
        resolved.org.keys.push(summary);
        // Fixture secret: presented once, never stored, never a real credential.
        return ok({ ...summary, secret: `sk-infrx-FAKE${suffix}${hex(resolved.org.counter, 24)}` });
      },

      async revoke(session, keyId) {
        const injectedResult = intercept<ApiKeySummary>("keys.revoke");
        if (injectedResult !== null) return injectedResult;
        const denied = requireOwner<ApiKeySummary>(session);
        if (denied !== null) return denied;
        const resolved = tenant<ApiKeySummary>(session);
        if (isError(resolved)) return resolved;
        const key = resolved.org.keys.find((candidate) => candidate.id === keyId);
        if (key === undefined) return fail("not_found", "no such key for this organization");
        if (key.revoked_at !== null) return fail("state_conflict", "this key is already revoked");
        key.revoked_at = nextTimestamp();
        return ok(structuredClone(key));
      },
    },

    async adminOrgs(session, query) {
      const injectedResult = intercept<Page<AdminOrgSummary>>("adminOrgs");
      if (injectedResult !== null) return injectedResult;
      const denied = requireOperator<Page<AdminOrgSummary>>(session);
      if (denied !== null) return denied;
      const cutoff = new Date(clockMs - 30 * 86400000).toISOString();
      const rows: AdminOrgSummary[] = [...orgs.values()].map((org) => ({
        org_id: org.org_id,
        name: org.name,
        owner_email: org.owner_email,
        created_at: org.created_at,
        suspended: org.suspended,
        balance: balanceOf(org),
        requests_30d: org.usage.filter((row) => row.created_at >= cutoff).length,
      }));
      rows.sort((a, b) => a.name.localeCompare(b.name));
      return paginate(rows, query, `operators|adminOrgs|${JSON.stringify(query.limit ?? DEFAULT_PAGE_LIMIT)}`);
    },

    async adminGrant(session, input) {
      const injectedResult = intercept<AdminGrantResult>("adminGrant");
      if (injectedResult !== null) return injectedResult;
      const denied = requireOperator<AdminGrantResult>(session);
      if (denied !== null) return denied;
      if (typeof input.idempotency_key !== "string" || input.idempotency_key.trim() === "") {
        return fail("invalid_request", "a grant needs an idempotency key");
      }
      if (typeof input.reason !== "string" || input.reason.trim() === "") {
        return fail("invalid_request", "a grant needs a reason");
      }
      if (input.kind !== "promotional") {
        return fail("invalid_request", "only promotional grants exist in the free pilot");
      }
      let amount: Money;
      try {
        amount = parseMoney(input.amount);
      } catch {
        return fail("invalid_request", "amount must be a USD decimal string");
      }
      if (isZeroMoney(amount) || isNegativeMoney(amount)) {
        return fail("invalid_request", "a grant amount must be positive");
      }
      const target = orgs.get(input.target_org_id);
      if (target === undefined) return fail("not_found", "no such organization");

      const payload = JSON.stringify({
        target_org_id: input.target_org_id,
        amount,
        kind: input.kind,
        reason: input.reason.trim(),
      });
      const previous = target.grants.get(input.idempotency_key);
      if (previous !== undefined) {
        if (previous.payload !== payload) {
          return fail("idempotency_conflict", "this idempotency key was used with a different payload");
        }
        return ok({ ...structuredClone(previous.result), replayed: true, balance: balanceOf(target) });
      }

      target.counter += 1;
      const entry: LedgerEntry = {
        id: deterministicUuid(8080, target.counter),
        created_at: nextTimestamp(),
        delta: amount,
        kind: "grant",
        reason: input.reason.trim(),
        ref: input.idempotency_key,
        actor: session.email,
      };
      target.ledger.unshift(entry);
      const result: AdminGrantResult = {
        grant_id: entry.id,
        org_id: target.org_id,
        amount,
        kind: "promotional",
        reason: entry.reason ?? "",
        created_at: entry.created_at,
        operator_principal: session.email,
        replayed: false,
        balance: balanceOf(target),
      };
      target.grants.set(input.idempotency_key, { payload, result });
      return ok(structuredClone(result));
    },

    async judgeRuns(session, query) {
      const injectedResult = intercept<Page<JudgeRun>>("judgeRuns");
      if (injectedResult !== null) return injectedResult;
      const resolved = tenant<Page<JudgeRun>>(session);
      if (isError(resolved)) return resolved;
      return paginate(resolved.org.judge, query, scopeOf(resolved.org, "judgeRuns", query));
    },
  };

  return services;
}
