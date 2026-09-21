/**
 * The console contract conformance suite (F-CONTRACT).
 *
 * The test bodies live here as one exported function taking a harness factory, so the
 * fixture-backed fake (tests/contracts/services.test.ts) and C's real implementation
 * (tests/c/) run the identical suite. Passing it on the fake means *implemented*, never
 * integrated: only the same suite against PostgreSQL/ClickHouse proves C.
 *
 * It asserts invariants, never fixture row counts, so it stays valid against real data.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import moneyCases from "../../tests/contracts/money_cases.json" with { type: "json" };
import { addMoney, compareMoney, isMoney, parseMoney, subMoney, ZERO_MONEY, type Money } from "./money.ts";
import {
  AUDIT_ACTIONS,
  AUTHOR_ROLES,
  CALIBRATION_LABELS,
  CREATABLE_LEDGER_ENTRY_KINDS,
  ENTITLEMENT_LIMIT_NAMES,
  ERROR_CODE_HTTP_STATUS,
  ERROR_CODES,
  EXECUTION_MODES,
  FEEDBACK_CHANNELS,
  FEEDBACK_ENTRY_NAMES,
  FEEDBACK_NAMES,
  IN_STREAM_ONLY_CODES,
  INTERNAL_ONLY_CODES,
  JOB_STATES,
  JUDGE_MODES,
  JUDGE_RUN_STATES,
  JUDGE_SCORE_KINDS,
  LEDGER_ENTRY_KINDS,
  MAX_CONTENT_RETENTION_DAYS,
  MAX_ENTITLEMENT_LIMIT,
  MAX_FEEDBACK_TEXT_CHARS,
  MAX_GRANT_REASON_CHARS,
  MAX_KEY_NAME_CHARS,
  MAX_IDEMPOTENCY_KEY_CHARS,
  MAX_PAGE_LIMIT,
  PLATFORM_ACTOR,
  SETTLEMENT_STATES,
  TERMINAL_CAUSES,
  TERMINAL_JOB_STATES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_LOSS_REASONS,
  TRACE_MODES,
  USAGE_CERTAINTIES,
  type AdminOrgSummary,
  type AuditEntry,
  type AuditQuery,
  type ConsentHistoryEntry,
  type FeedbackEntry,
  type JudgeRun,
  type LedgerEntry,
  type Page,
  type Result,
  type SessionContext,
  type TraceDetail,
  type TraceListItem,
  type UsageRow,
} from "./types.ts";
import {
  CONSOLE_OPERATIONS,
  OPERATIONS_WITHOUT_INPUT_OBJECT,
  OPERATOR_ONLY_OPERATIONS,
  OWNER_ONLY_OPERATIONS,
  OWNER_OR_OPERATOR_OPERATIONS,
  MUTATING_OPERATIONS,
  SUSPENDED_REFUSED_OPERATIONS,
  type ConsoleServices,
} from "./services.ts";

export type ConsoleHarness = {
  services: ConsoleServices;
  sessions: {
    owner: SessionContext;
    member: SessionContext;
    /** A platform operator who also owns `ids.orgId`. */
    operator: SessionContext;
    /**
     * A platform operator whose organization role is only `member`. Operator authority is the flag
     * and nothing else; a suite that only ever uses `operator` cannot tell an implementation that
     * checks the flag from one that happens to check the role.
     */
    operatorMember: SessionContext;
    otherOwner: SessionContext;
    /** R18: the owner of an already-suspended organization. */
    suspendedOwner: SessionContext;
  };
  ids: {
    orgId: string;
    otherOrgId: string;
    availableRequestId: string;
    offRequestId: string;
    otherOrgRequestId: string;
    unknownRequestId: string;
    keyId: string;
    otherOrgKeyId: string;
    /** A model id the platform serves. */
    modelId: string;
    /** R18: an organization that is suspended before the suite touches anything. */
    suspendedOrgId: string;
  };
};

/**
 * What the harness must guarantee beyond the identifiers, because the suite cannot create it:
 * enough rows for several pages, **and at least one group of rows sharing a `created_at`** in
 * usage, the ledger and traces. Equal timestamps are where a keyset cursor either holds or quietly
 * drops a row, so a harness without them leaves the interesting case untested (N1).
 */

export type ConsoleHarnessFactory = () => ConsoleHarness | Promise<ConsoleHarness>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

function inSet(allowed: readonly string[], value: unknown): boolean {
  return typeof value === "string" && allowed.includes(value);
}

function expectOk<T>(result: Result<T>, what: string): T {
  assert.ok(result.ok, `${what} failed: ${result.ok ? "" : `${result.error.code} ${result.error.message}`}`);
  return result.value;
}

function expectError<T>(result: Result<T>, code: string, what: string): void {
  assert.ok(!result.ok, `${what} should have failed with ${code}`);
  assert.equal(result.error.code, code, what);
  assert.ok(inSet(ERROR_CODES, result.error.code), `${what}: unknown error code`);
  assert.ok(result.error.message.length > 0, `${what}: empty message`);
}

/**
 * 08 §9: a failure is a `Result`, never an exception. A rejected promise is its own defect — it
 * escapes the error rendering every console page is built on — so every call the suite makes about
 * refusal goes through here.
 */
async function settle<T>(what: string, call: () => Promise<Result<T>>): Promise<Result<T>> {
  try {
    return await call();
  } catch (error) {
    assert.fail(`${what} threw instead of returning a Result error: ${String(error)}`);
  }
}

const LARGEST_MONEY = "999999999999.99999999" as Money;

/** The cross-language accept/reject list (R11), fed through the interface by the case below. */
const MONEY_CASES = moneyCases as unknown as { input: string; valid: boolean; canonical: string | null }[];

/** Walk every page, with a hard stop so a broken cursor cannot loop forever. */
async function walkAll<T>(
  fetch: (cursor: string | null) => Promise<Result<Page<T>>>,
  what: string,
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;
  for (let pages = 0; pages < 500; pages += 1) {
    const page: Page<T> = expectOk(await fetch(cursor), `${what} page ${pages}`);
    items.push(...page.items);
    if (page.next_cursor === null) return items;
    assert.ok(page.items.length > 0, `${what}: a non-final page must not be empty`);
    cursor = page.next_cursor;
  }
  // `assert.fail` keeps a pagination bug a *failed assertion* rather than an exception:
  // the mutation runner classifies an exception as a runner error and refuses the kill,
  // so a mutant that broke cursor termination would have been reported as unkillable.
  return assert.fail(`${what}: pagination did not terminate`);
}

/**
 * The same query walked at two page sizes must yield the identical ordered id list. Without this,
 * a service that skips one row per page boundary passes a duplicate check and an order check --
 * the classic keyset off-by-one -- while silently losing rows.
 */
async function assertSamePagesAtEveryLimit<T>(
  fetch: (limit: number, cursor: string | null) => Promise<Result<Page<T>>>,
  idOf: (row: T) => string,
  what: string,
): Promise<T[]> {
  const small = await walkAll<T>((cursor) => fetch(7, cursor), `${what} at limit 7`);
  const large = await walkAll<T>((cursor) => fetch(MAX_PAGE_LIMIT, cursor), `${what} at limit ${MAX_PAGE_LIMIT}`);
  assert.deepEqual(
    small.map(idOf),
    large.map(idOf),
    `${what}: walking at two page sizes returned different rows, so a page boundary drops or repeats one`,
  );
  const ids = small.map(idOf);
  assert.equal(new Set(ids).size, ids.length, `${what}: a row was returned twice`);
  return small;
}

function assertDescending(values: string[], what: string): void {
  for (let i = 1; i < values.length; i += 1) {
    assert.ok(values[i - 1] >= values[i], `${what}: order is not stable and descending at ${i}`);
  }
}

/**
 * The order must be *total*: newest first by `created_at`, ties broken by id, and no two rows equal
 * on both. A merely "descending by created_at" list has an undefined order inside a group of equal
 * timestamps, and a keyset cursor across that group then skips or repeats a row (N1).
 */
function assertStrictTotalOrder(rows: { at: string; id: string }[], what: string): void {
  for (let i = 1; i < rows.length; i += 1) {
    const previous = rows[i - 1];
    const current = rows[i];
    assert.ok(
      previous.at > current.at || (previous.at === current.at && previous.id > current.id),
      `${what}: (created_at, id) is not strictly descending at ${i}: ${JSON.stringify(previous)} then ${JSON.stringify(current)}`,
    );
  }
}

/** The harness must contain the interesting case, or the assertion above proves nothing. */
/**
 * A keyset cursor compares `created_at` as a string, so every row in one list must use the same
 * timestamp format. `…:00Z` and `…:00.000Z` for the same instant would sort differently and a walk
 * across the two forms would skip rows.
 */
function assertComparableTimestamps(rows: { at: string }[], what: string): void {
  const widths = new Set(rows.map((row) => row.at.length));
  assert.ok(widths.size <= 1, `${what}: mixed timestamp formats (${[...widths].join(", ")} characters) cannot be ordered`);
  for (const row of rows) assert.match(row.at, RFC3339, `${what}: timestamp format`);
}

function assertHasTimestampTie(rows: { at: string; id: string }[], what: string): void {
  const tied = rows.some((row, index) => index > 0 && rows[index - 1].at === row.at);
  assert.ok(
    tied,
    `${what}: the harness needs at least two rows sharing a created_at, or the keyset tie-break is untested`,
  );
}

/** R19: an operator label is the only entry that may claim operator authorship or membership. */
/**
 * Q16: the exact field set of a DTO, not a subset.
 *
 * Every assertion that only checks the fields it names passes just as happily on a row
 * carrying extra ones — and the fake's own stored shapes carry `by_operator`, which is
 * precisely what R41 says a customer must never receive. Pinning the set is what turns
 * "the projection dropped it" into something a test can see.
 */
function assertExactFields(row: object, expected: readonly string[], what: string): void {
  assert.deepEqual(
    Object.keys(row).sort(),
    [...expected].sort(),
    `${what}: the DTO must carry exactly these fields, no more and no fewer`,
  );
}

const FEEDBACK_ENTRY_FIELDS = [
  "id",
  "request_id",
  "created_at",
  "channel",
  "author_role",
  "author_principal",
  "name",
  "value",
  "comment",
  "calibration_set",
  "rubric_version",
] as const;

const CONSENT_HISTORY_ENTRY_FIELDS = ["changed_at", "evaluation_consent", "changed_by"] as const;

const LEDGER_ENTRY_FIELDS = ["id", "created_at", "delta", "kind", "reason", "ref", "actor"] as const;

function assertConsentHistoryEntry(entry: ConsentHistoryEntry, what: string): void {
  // The stored entry carries `by_operator`; the DTO must not (R41).
  assertExactFields(entry, CONSENT_HISTORY_ENTRY_FIELDS, what);
  assert.match(entry.changed_at, RFC3339, `${what}: changed_at`);
  assert.equal(typeof entry.evaluation_consent, "boolean", `${what}: evaluation_consent`);
  assert.ok(entry.changed_by.length > 0, `${what}: changed_by`);
}

function assertFeedbackEntry(entry: FeedbackEntry, what: string): void {
  assertExactFields(entry, FEEDBACK_ENTRY_FIELDS, what);
  assert.ok(inSet(FEEDBACK_ENTRY_NAMES, entry.name), `${what}: unknown feedback name ${entry.name}`);
  assert.ok(inSet(FEEDBACK_CHANNELS, entry.channel), `${what}: feedback channel`);
  assert.ok(inSet(AUTHOR_ROLES, entry.author_role), `${what}: feedback author role`);
  if (entry.author_role === "operator") {
    assert.equal(entry.name, "calibration_label", `${what}: only a calibration label is operator-authored`);
  }
  if (entry.calibration_set) {
    assert.equal(entry.author_role, "operator", `${what}: calibration membership needs operator authority`);
    assert.equal(entry.name, "calibration_label", `${what}: only a calibration label is in the set`);
  }
  assert.equal(
    entry.rubric_version === null,
    entry.name !== "calibration_label",
    `${what}: a rubric version belongs to a calibration label and nothing else`,
  );
  if (entry.name === "calibration_label") {
    assert.ok(inSet(CALIBRATION_LABELS, entry.value as string), `${what}: calibration label value`);
  }
}

function assertUsageRow(row: UsageRow): void {
  assert.match(row.request_id, UUID, "usage request_id");
  assert.match(row.created_at, RFC3339, "usage created_at");
  assert.ok(inSet(JOB_STATES, row.job_state), "usage job_state");
  assert.ok(row.terminal_cause === null || inSet(TERMINAL_CAUSES, row.terminal_cause), "usage terminal_cause");
  assert.ok(inSet(EXECUTION_MODES, row.execution_mode), "usage execution_mode");
  assert.ok(inSet(USAGE_CERTAINTIES, row.usage_certainty), "usage usage_certainty");
  // R13: the settlement column is null until the request is terminal, and set once it is.
  assert.ok(
    row.settlement_state === null || inSet(SETTLEMENT_STATES, row.settlement_state),
    "usage settlement_state",
  );
  assert.equal(
    row.settlement_state === null,
    !inSet(TERMINAL_JOB_STATES, row.job_state),
    `usage ${row.request_id}: ${row.job_state} must ${inSet(TERMINAL_JOB_STATES, row.job_state) ? "" : "not "}carry a settlement state`,
  );
  assert.ok(inSet(TRACE_MODES, row.trace_mode), "usage trace_mode");
  assert.ok(isMoney(row.cost), `usage cost is not canonical money: ${row.cost}`);
  assert.ok(row.max_hold === null || isMoney(row.max_hold), "usage max_hold");
  assert.ok(Number.isInteger(row.http_status), "usage http_status");
  // An estimate never masquerades as an authoritative charge.
  if (row.usage_certainty === "unknown") {
    assert.equal(row.prompt_tokens, null, "unknown usage must not report prompt tokens");
    assert.equal(row.completion_tokens, null, "unknown usage must not report completion tokens");
    assert.equal(row.cost, ZERO_MONEY, "unknown usage must not be charged");
  }
  if (row.settlement_state !== "settled") {
    assert.equal(row.cost, ZERO_MONEY, "only a settled request carries a charge");
  }
}

function assertLedgerEntry(entry: LedgerEntry): void {
  assertExactFields(entry, LEDGER_ENTRY_FIELDS, `ledger ${entry.id}`);
  assert.match(entry.created_at, RFC3339, "ledger created_at");
  assert.ok(inSet(LEDGER_ENTRY_KINDS, entry.kind), "ledger kind");
  assert.ok(isMoney(entry.delta), `ledger delta is not canonical money: ${entry.delta}`);
  if (entry.kind === "grant") {
    assert.equal(compareMoney(entry.delta, ZERO_MONEY), 1, "a grant is positive");
    assert.ok(entry.reason !== null && entry.reason.length > 0, "a grant needs a reason");
  }
  if (entry.kind === "usage") {
    assert.ok(compareMoney(entry.delta, ZERO_MONEY) <= 0, "a usage entry is a debit");
  }
  if (entry.kind === "purchase") {
    // R13: a legacy row must render. It is never created, which the mutation suite asserts.
    assert.equal(compareMoney(entry.delta, ZERO_MONEY), 1, "a purchase is a credit");
  }
}

function assertTraceListItem(row: TraceListItem): void {
  assert.match(row.request_id, UUID, "trace request_id");
  assert.ok(inSet(TRACE_MODES, row.trace_mode), "trace trace_mode");
  assert.ok(inSet(TRACE_CONTENT_AVAILABILITY, row.content), "trace content availability");
  assert.ok(inSet(TRACE_LOSS_REASONS, row.loss_reason), "trace loss_reason");
  assert.ok(isMoney(row.cost), "trace cost");
  assert.ok(Number.isInteger(row.feedback_count) && row.feedback_count >= 0, "trace feedback_count");
  // R13: an off-mode request has no trace row at all, so it can never appear in a trace list.
  assert.notEqual(row.trace_mode, "off", `trace ${row.request_id}: an off-mode request has no trace row`);
  assert.notEqual(row.content, "off", `trace ${row.request_id}: an off-mode row cannot be listed`);
  if (row.trace_mode === "minimal") {
    assert.equal(row.content, "metadata_only", "minimal capture stores metadata only");
  }
  if (row.content === "lost") {
    assert.notEqual(row.loss_reason, "none", "lost content must say why");
  }
}

function assertTraceDetail(detail: TraceDetail): void {
  assertTraceListItem(detail);
  assert.ok(detail.error_code === null || inSet(ERROR_CODES, detail.error_code), "trace error_code");
  // 08 §3 freezes one HTTP status per code: 500 internal_error, 503 dependency_unavailable,
  // 504 deadline_exceeded. A row that pairs them differently would teach V the wrong envelope.
  if (detail.error_code !== null) {
    assert.equal(
      ERROR_CODE_HTTP_STATUS[detail.error_code],
      detail.http_status,
      `trace ${detail.request_id}: HTTP ${detail.http_status} is not the status of ${detail.error_code}`,
    );
  }
  if (detail.http_status >= 400) {
    assert.ok(detail.error_code !== null, `HTTP ${detail.http_status} must carry an error code`);
  }
  // Content cannot expire before it was captured, nor metadata before the content it describes.
  if (detail.content_expires_at !== null) {
    assert.ok(
      detail.content_expires_at > detail.created_at,
      `trace ${detail.request_id}: content expires at ${detail.content_expires_at}, before it was created`,
    );
  }
  assert.ok(
    detail.metadata_expires_at > detail.created_at,
    `trace ${detail.request_id}: metadata expires before it was created`,
  );
}

function assertJudgeRun(run: JudgeRun): void {
  assert.ok(inSet(JUDGE_RUN_STATES, run.state), "judge state");
  assert.ok(inSet(JUDGE_MODES, run.mode), "judge mode");
  assert.ok(isMoney(run.budget_reserved), "judge budget_reserved");
  assert.ok(run.budget_settled === null || isMoney(run.budget_settled), "judge budget_settled");
  assert.equal(run.sample_count, run.samples.length, "judge sample_count");
  assert.equal(
    run.limited_evaluation_count,
    run.samples.filter((sample) => sample.limited_evaluation).length,
    "judge limited_evaluation_count",
  );
  if (run.state === "ambiguous") {
    // 02: an ambiguous submission holds its reservation and is never resubmitted.
    assert.equal(run.budget_settled, null, "an ambiguous run is not settled");
    assert.equal(compareMoney(run.budget_reserved, ZERO_MONEY), 1, "an ambiguous run still holds budget");
    assert.ok(run.quarantine_reason !== null, "an ambiguous run records why");
  }
  for (const sample of run.samples) {
    assert.equal(sample.run_id, run.id, "sample run_id");
    if (sample.limited_evaluation) {
      assert.ok(sample.limited_reason !== null, "a limited evaluation says why");
      assert.equal(
        sample.scores.filter((score) => score.name === "groundedness").length,
        0,
        "no media means no groundedness score",
      );
    }
    for (const score of sample.scores) {
      assert.ok(inSet(JUDGE_SCORE_KINDS, score.kind), "score kind");
      assert.equal(score.rubric_version, run.rubric_version, "score rubric version");
      assert.equal(score.judge_model, run.judge_model, "score judge model");
      // A dry run produces estimates only; a provider result is never an estimate.
      assert.equal(score.estimated, run.mode === "dry_run", "score estimated flag follows the run mode");
    }
  }
}

/**
 * One probe per operation, so the cases that must cover *every* operation are driven from
 * `CONSOLE_OPERATIONS` rather than from a hand-kept list that a new operation can slip past.
 * `call` is the operation performed legitimately; `withExtraField` repeats it with one invented
 * field, for the operations that take an object.
 */
export type OperationProbe = {
  call(session: SessionContext): Promise<Result<unknown>>;
  withExtraField?(session: SessionContext): Promise<Result<unknown>>;
  /** The same operation handed something that is not an object at all. */
  asGiven(session: SessionContext, input: unknown): Promise<Result<unknown>>;
  /** True when the result does not depend on the caller's organization (operator-wide lists). */
  tenantIndependent?: boolean;
};

export function operationProbes(
  services: ConsoleServices,
  sessions: ConsoleHarness["sessions"],
  ids: ConsoleHarness["ids"],
): Record<string, OperationProbe> {
  // One invented field, the most tempting one: the tenant. `as never` at each call site keeps the
  // compiler honest about the fact that no input declares it.
  const extra: Record<string, string> = { org_id: ids.otherOrgId };
  const grant = (key: string) => ({
    target_org_id: ids.otherOrgId,
    amount: "1.00000000" as Money,
    kind: "promotional" as const,
    reason: "probe",
    idempotency_key: key,
  });
  let unique = 0;
  const nextKey = (name: string): string => {
    unique += 1;
    return `probe-${name}-${unique}`;
  };
  return {
    usage: {
      asGiven: (session, input) => services.usage(session, input as never),
      call: (session) => services.usage(session, { limit: 2 }),
      withExtraField: (session) => services.usage(session, { limit: 2, ...extra }),
    },
    usageSummary: {
      asGiven: (session, input) => services.usageSummary(session, input as never),
      call: (session) => services.usageSummary(session, {}),
      withExtraField: (session) => services.usageSummary(session, { ...extra }),
    },
    usageDaily: {
      asGiven: (session, input) => services.usageDaily(session, input as never),
      call: (session) => services.usageDaily(session, {}),
      withExtraField: (session) => services.usageDaily(session, { ...extra }),
    },
    balances: {
      call: (session) => services.balances(session),
      asGiven: (session) => services.balances(session),
    },
    ledger: {
      asGiven: (session, input) => services.ledger(session, input as never),
      call: (session) => services.ledger(session, { limit: 2 }),
      withExtraField: (session) => services.ledger(session, { limit: 2, ...extra }),
    },
    traces: {
      asGiven: (session, input) => services.traces(session, input as never),
      call: (session) => services.traces(session, { limit: 2 }),
      withExtraField: (session) => services.traces(session, { limit: 2, ...extra }),
    },
    traceDetail: {
      call: (session) => services.traceDetail(session, ids.availableRequestId),
      asGiven: (session) => services.traceDetail(session, ids.availableRequestId),
    },
    traceContent: {
      call: (session) => services.traceContent(session, ids.availableRequestId),
      asGiven: (session) => services.traceContent(session, ids.availableRequestId),
    },
    "feedback.list": {
      call: (session) => services.feedback.list(session, ids.availableRequestId),
      asGiven: (session) => services.feedback.list(session, ids.availableRequestId),
    },
    "feedback.submit": {
      asGiven: (session, input) => services.feedback.submit(session, input as never),
      call: (session) =>
        services.feedback.submit(session, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: nextKey("feedback"),
        }),
      withExtraField: (session) =>
        services.feedback.submit(session, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: nextKey("feedback-extra"),
          ...extra,
        } as never),
    },
    "calibration.label": {
      asGiven: (session, input) => services.calibration.label(session, input as never),
      call: (session) =>
        services.calibration.label(session, {
          request_id: ids.availableRequestId,
          rubric_version: 1,
          label: "correct",
          idempotency_key: nextKey("label"),
        }),
      withExtraField: (session) =>
        services.calibration.label(session, {
          request_id: ids.availableRequestId,
          rubric_version: 1,
          label: "correct",
          idempotency_key: nextKey("label-extra"),
          ...extra,
        } as never),
      tenantIndependent: true,
    },
    "calibration.list": {
      asGiven: (session, input) => services.calibration.list(session, input as never),
      call: (session) => services.calibration.list(session, { limit: 2 }),
      withExtraField: (session) => services.calibration.list(session, { limit: 2, ...extra }),
      tenantIndependent: true,
    },
    "settings.get": {
      call: (session) => services.settings.get(session),
      asGiven: (session) => services.settings.get(session),
    },
    "settings.update": {
      asGiven: (session, input) => services.settings.update(session, input as never),
      call: (session) => services.settings.update(session, { trace_mode: "full" }),
      withExtraField: (session) => services.settings.update(session, { trace_mode: "full", ...extra } as never),
    },
    "keys.list": {
      call: (session) => services.keys.list(session),
      asGiven: (session) => services.keys.list(session),
    },
    "keys.create": {
      asGiven: (session, input) => services.keys.create(session, input as never),
      call: (session) => services.keys.create(session, { name: `probe ${nextKey("key")}` }),
      withExtraField: (session) =>
        services.keys.create(session, { name: `probe ${nextKey("key-extra")}`, ...extra } as never),
    },
    "keys.revoke": {
      asGiven: (session) => services.keys.revoke(session, ids.keyId),
      call: async (session) => {
        const listed = await services.keys.list(session);
        if (!listed.ok) return listed;
        const active = listed.value.find((key) => key.revoked_at === null);
        if (active === undefined) return { ok: false, error: { code: "not_found", message: "no active key" } };
        return services.keys.revoke(session, active.id);
      },
    },
    adminOrgs: {
      asGiven: (session, input) => services.adminOrgs(session, input as never),
      call: (session) => services.adminOrgs(session, { limit: 2 }),
      withExtraField: (session) => services.adminOrgs(session, { limit: 2, ...extra }),
      tenantIndependent: true,
    },
    adminGrant: {
      asGiven: (session, input) => services.adminGrant(session, input as never),
      call: (session) => services.adminGrant(session, grant(nextKey("grant"))),
      withExtraField: (session) => services.adminGrant(session, { ...grant(nextKey("grant-extra")), ...extra } as never),
      tenantIndependent: true,
    },
    adminSetSuspension: {
      asGiven: (session, input) => services.adminSetSuspension(session, input as never),
      call: (session) =>
        services.adminSetSuspension(session, {
          target_org_id: ids.otherOrgId,
          suspended: false,
          reason: "probe",
          idempotency_key: nextKey("suspension"),
        }),
      withExtraField: (session) =>
        services.adminSetSuspension(session, {
          target_org_id: ids.otherOrgId,
          suspended: false,
          reason: "probe",
          idempotency_key: nextKey("suspension-extra"),
          ...extra,
        } as never),
      tenantIndependent: true,
    },
    adminSetEntitlements: {
      asGiven: (session, input) => services.adminSetEntitlements(session, input as never),
      call: (session) =>
        services.adminSetEntitlements(session, {
          target_org_id: ids.otherOrgId,
          model_ids: null,
          limits: {},
          reason: "probe",
          idempotency_key: nextKey("entitlements"),
        }),
      withExtraField: (session) =>
        services.adminSetEntitlements(session, {
          target_org_id: ids.otherOrgId,
          model_ids: null,
          limits: {},
          reason: "probe",
          idempotency_key: nextKey("entitlements-extra"),
          ...extra,
        } as never),
      tenantIndependent: true,
    },
    adminAudit: {
      asGiven: (session, input) => services.adminAudit(session, input as never),
      call: (session) => services.adminAudit(session, { limit: 2 }),
      withExtraField: (session) => services.adminAudit(session, { limit: 2, actor: "someone" } as never),
      tenantIndependent: true,
    },
    judgeRuns: {
      asGiven: (session, input) => services.judgeRuns(session, input as never),
      call: (session) => services.judgeRuns(session, { limit: 2 }),
      withExtraField: (session) => services.judgeRuns(session, { limit: 2, ...extra }),
    },
  };
}

export function assertProbesCoverEveryOperation(probes: Record<string, OperationProbe>): void {
  assert.deepEqual(
    Object.keys(probes).sort(),
    [...CONSOLE_OPERATIONS].sort(),
    "every operation needs a probe, so a new one cannot escape the role, suspension and input cases",
  );
  const takesObject = CONSOLE_OPERATIONS.filter(
    (operation) => !(OPERATIONS_WITHOUT_INPUT_OBJECT as readonly string[]).includes(operation),
  );
  assert.deepEqual(
    Object.entries(probes)
      .filter(([, probe]) => probe.withExtraField !== undefined)
      .map(([name]) => name)
      .sort(),
    [...takesObject].sort(),
    "every operation that takes an object must be probed with an invented field",
  );
}

export function runConsoleServicesConformance(
  makeHarness: ConsoleHarnessFactory,
  label = "ConsoleServices",
): void {
  describe(label, () => {
    it("usage pages walk every row exactly once, newest first", async () => {
      const { services, sessions } = await makeHarness();
      const all = await assertSamePagesAtEveryLimit<UsageRow>(
        (limit, cursor) => services.usage(sessions.owner, { limit, cursor }),
        (row) => row.request_id,
        "usage",
      );
      const single = expectOk(
        await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }),
        "usage single page",
      );
      assert.ok(all.length >= single.items.length, "paged walk lost rows");
      assertDescending(
        all.map((row) => row.created_at),
        "usage",
      );
      const keys = all.map((row) => ({ at: row.created_at, id: row.request_id }));
      assertComparableTimestamps(keys, "keys");
      assertHasTimestampTie(keys, "usage");
      assertStrictTotalOrder(keys, "usage");
      for (const row of all) assertUsageRow(row);
    });

    it("ledger and trace pages walk every row exactly once", async () => {
      const { services, sessions } = await makeHarness();
      const ledger = await assertSamePagesAtEveryLimit<LedgerEntry>(
        (limit, cursor) => services.ledger(sessions.owner, { limit, cursor }),
        (entry) => entry.id,
        "ledger",
      );
      assertDescending(
        ledger.map((entry) => entry.created_at),
        "ledger",
      );
      const ledgerKeys = ledger.map((entry) => ({ at: entry.created_at, id: entry.id }));
      assertComparableTimestamps(ledgerKeys, "ledgerKeys");
      assertHasTimestampTie(ledgerKeys, "ledger");
      assertStrictTotalOrder(ledgerKeys, "ledger");
      for (const entry of ledger) assertLedgerEntry(entry);

      const traces = await assertSamePagesAtEveryLimit<TraceListItem>(
        (limit, cursor) => services.traces(sessions.owner, { limit, cursor }),
        (row) => row.request_id,
        "traces",
      );
      // A list row is the list shape and nothing more: a service that returned its detail object
      // would pass every other assertion here while leaking whatever the detail carries.
      const listFields = [
        "request_id",
        "created_at",
        "model",
        "key_id",
        "job_state",
        "http_status",
        "trace_mode",
        "content",
        "loss_reason",
        "prompt_tokens",
        "completion_tokens",
        "ttft_ms",
        "wall_ms",
        "cost",
        "feedback_count",
        "score_count",
      ].sort();
      for (const row of traces) {
        assert.deepEqual(Object.keys(row).sort(), listFields, "a trace list row must be exactly the list shape");
      }
      const traceKeys = traces.map((row) => ({ at: row.created_at, id: row.request_id }));
      assertComparableTimestamps(traceKeys, "traceKeys");
      assertHasTimestampTie(traceKeys, "traces");
      assertStrictTotalOrder(traceKeys, "traces");
      for (const row of traces) assertTraceListItem(row);
    });

    it("rejects a limit above the hard bound and a limit that is not a positive integer", async () => {
      const { services, sessions } = await makeHarness();
      expectError(
        await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT + 1 }),
        "invalid_request",
        "usage limit 101",
      );
      expectError(await services.ledger(sessions.owner, { limit: 1000 }), "invalid_request", "ledger limit 1000");
      expectError(await services.traces(sessions.owner, { limit: 0 }), "invalid_request", "traces limit 0");
      expectError(await services.usage(sessions.owner, { limit: -5 }), "invalid_request", "usage limit -5");
      expectError(await services.usage(sessions.owner, { limit: 2.5 }), "invalid_request", "usage limit 2.5");
      expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }), "usage limit 100");
    });

    it("rejects a cursor it did not issue for this query", async () => {
      const { services, sessions, ids } = await makeHarness();
      const first = expectOk(await services.usage(sessions.owner, { limit: 5 }), "usage first page");
      assert.ok(first.next_cursor !== null, "the fixture needs more than one page");
      const cursor = first.next_cursor;

      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: "not-a-cursor" }),
        "invalid_cursor",
        "garbage cursor",
      );
      // R36: a cursor is opaque here. Every forgery below is black box — a cursor from another
      // list, another tenant or another filter, or this one with its characters disturbed — so an
      // implementation that signs or encrypts its cursors passes unchanged. Shape-aware forgeries
      // live in the implementation's own tests.
      for (const [mutated, what] of [
        [`${cursor}x`, "a character appended"],
        [cursor.slice(0, -1), "the last character removed"],
        [`x${cursor}`, "a character prepended"],
        [cursor.split("").reverse().join(""), "the characters reversed"],
        [cursor.toUpperCase() === cursor ? cursor.toLowerCase() : cursor.toUpperCase(), "the case flipped"],
      ] as [string, string][]) {
        if (mutated === cursor) continue;
        expectError(
          await services.usage(sessions.owner, { limit: 5, cursor: mutated }),
          "invalid_cursor",
          `cursor with ${what}`,
        );
      }
      const foreign = expectOk(
        await services.traces(sessions.owner, { limit: 5 }),
        "a page from another query",
      ).next_cursor;
      assert.ok(foreign !== null, "the harness needs more than one page of traces");
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: foreign }),
        "invalid_cursor",
        "a cursor minted for another list",
      );
      // A cursor is bound to the query that produced it, so filters cannot change mid-walk.
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor, model: ids.modelId }),
        "invalid_cursor",
        "cursor reused with a different filter",
      );
      expectOk(await services.usage(sessions.owner, { limit: 5, cursor }), "reissued cursor");
    });

    it("balance is the ledger total minus reservations, and holds reduce what is available", async () => {
      const { services, sessions, ids } = await makeHarness();
      const balance = expectOk(await services.balances(sessions.owner), "balances");
      for (const value of [balance.ledger_total, balance.reserved_total, balance.available]) {
        assert.ok(isMoney(value), `balance value is not canonical money: ${value}`);
      }
      assert.equal(
        balance.available,
        subMoney(balance.ledger_total, balance.reserved_total),
        "available must be total minus reserved, exactly",
      );
      const ledger = await walkAll<LedgerEntry>(
        (cursor) => services.ledger(sessions.owner, { limit: 50, cursor }),
        "ledger",
      );
      let total = ZERO_MONEY;
      for (const entry of ledger) total = addMoney(total, entry.delta);
      assert.equal(balance.ledger_total, total, "the ledger total must equal the sum of its entries");

      // The reserved total is the sum of the outstanding holds — not "at least", not "something
      // non-zero". A harness without an outstanding hold cannot test this, so it is required.
      const rows = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "usage",
      );
      const holds = rows.filter((row) => row.max_hold !== null).map((row) => row.max_hold as Money);
      assert.ok(holds.length > 0, "the harness must have at least one outstanding hold");
      let expectedReserved = ZERO_MONEY;
      for (const hold of holds) expectedReserved = addMoney(expectedReserved, hold);
      assert.equal(
        balance.reserved_total,
        expectedReserved,
        "reserved_total must be the sum of every outstanding max_hold",
      );
      assert.equal(compareMoney(balance.reserved_total, ZERO_MONEY), 1, "and that sum is not zero here");
      assert.equal(compareMoney(balance.available, balance.ledger_total), -1, "a hold must reduce available");

      const usage = expectOk(await services.usageSummary(sessions.owner, {}), "usage summary");
      assert.ok(isMoney(usage.cost), "summary cost");
      assert.ok(isMoney(usage.pending_reconciliation), "summary pending_reconciliation");
      // Unknown usage is held, not charged, so the figure a customer reads as "awaiting
      // reconciliation" must be the sum of those holds and never a constant.
      let expectedPending = ZERO_MONEY;
      for (const row of rows) {
        if (row.usage_certainty === "unknown" && row.max_hold !== null) {
          expectedPending = addMoney(expectedPending, row.max_hold);
        }
      }
      assert.equal(usage.pending_reconciliation, expectedPending, "pending_reconciliation is the held sum");
      assert.equal(compareMoney(usage.pending_reconciliation, ZERO_MONEY), 1, "which is not zero here");
      // And the operator's view of the same wallet agrees with the customer's.
      const operatorView = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      ).items.find((org) => org.org_id === ids.orgId);
      assert.ok(operatorView !== undefined, "the organization must be listed for the operator");
      assert.deepEqual(operatorView.balance, balance, "the operator sees the same wallet, not zeros");
    });

    it("a new organization starts at zero with no ledger history", async () => {
      const { services, sessions } = await makeHarness();
      const balance = expectOk(await services.balances(sessions.otherOwner), "new org balances");
      assert.equal(balance.ledger_total, ZERO_MONEY, "a new organization has no balance");
      assert.equal(balance.available, ZERO_MONEY, "a new organization has nothing available");
      const ledger = expectOk(await services.ledger(sessions.otherOwner, {}), "new org ledger");
      assert.equal(ledger.items.length, 0, "a new organization has no ledger entries");
      assert.equal(ledger.next_cursor, null);
    });

    it("identifiers from another organization are not_found, in both directions", async () => {
      const { services, sessions, ids } = await makeHarness();
      expectError(
        await services.traceDetail(sessions.owner, ids.otherOrgRequestId),
        "not_found",
        "cross-tenant trace detail",
      );
      expectError(
        await services.traceContent(sessions.owner, ids.otherOrgRequestId),
        "not_found",
        "cross-tenant trace content",
      );
      expectError(
        await services.feedback.list(sessions.owner, ids.otherOrgRequestId),
        "not_found",
        "cross-tenant feedback list",
      );
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.otherOrgRequestId,
          name: "thumb",
          value: true,
          idempotency_key: "cross-tenant-feedback-1",
        }),
        "not_found",
        "cross-tenant feedback submit",
      );
      expectError(
        await services.keys.revoke(sessions.owner, ids.otherOrgKeyId),
        "not_found",
        "cross-tenant key revocation",
      );
      expectError(
        await services.traceDetail(sessions.otherOwner, ids.availableRequestId),
        "not_found",
        "reverse cross-tenant trace detail",
      );
      expectError(
        await services.traceDetail(sessions.owner, ids.unknownRequestId),
        "not_found",
        "unknown request id",
      );
    });

    it("a filter or cursor from another organization never widens the tenant", async () => {
      const { services, sessions, ids } = await makeHarness();
      const ownKeys = new Set(
        expectOk(await services.keys.list(sessions.owner), "owner keys").map((key) => key.id),
      );
      assert.ok(!ownKeys.has(ids.otherOrgKeyId), "the other organization's key must not be listed");

      // Supplying another tenant's key id filters, it never joins: own rows or none, never theirs.
      for (const rows of [
        expectOk(await services.usage(sessions.owner, { key_id: ids.otherOrgKeyId }), "usage by foreign key").items,
        expectOk(await services.traces(sessions.owner, { key_id: ids.otherOrgKeyId }), "traces by foreign key").items,
      ]) {
        for (const row of rows) {
          assert.ok(ownKeys.has(row.key_id), `a foreign key filter returned ${row.key_id}`);
        }
      }

      // A cursor is bound to its tenant as well as to its filters.
      const page = expectOk(await services.usage(sessions.owner, { limit: 5 }), "owner usage page");
      assert.ok(page.next_cursor !== null, "the harness needs more than one page");
      expectError(
        await services.usage(sessions.otherOwner, { limit: 5, cursor: page.next_cursor }),
        "invalid_cursor",
        "a cursor minted for one organization used by another",
      );

      // Nothing tenant-scoped leaks the other way either.
      const ownTraces = new Set(
        expectOk(await services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT }), "owner traces").items.map(
          (row) => row.request_id,
        ),
      );
      const foreignTraces = expectOk(
        await services.traces(sessions.otherOwner, { limit: MAX_PAGE_LIMIT }),
        "other organization traces",
      );
      for (const row of foreignTraces.items) {
        assert.ok(!ownTraces.has(row.request_id), `trace ${row.request_id} is visible to both tenants`);
      }
      const ownRuns = new Set(
        expectOk(await services.judgeRuns(sessions.owner, { limit: MAX_PAGE_LIMIT }), "owner judge runs").items.map(
          (run) => run.id,
        ),
      );
      for (const run of expectOk(
        await services.judgeRuns(sessions.otherOwner, { limit: MAX_PAGE_LIMIT }),
        "other organization judge runs",
      ).items) {
        assert.ok(!ownRuns.has(run.id), `judge run ${run.id} is visible to both tenants`);
      }
      const foreignKeys = expectOk(await services.keys.list(sessions.otherOwner), "other organization keys");
      for (const key of foreignKeys) {
        assert.ok(!ownKeys.has(key.id), `key ${key.id} is visible to both tenants`);
      }
    });

    it("a filter outside its vocabulary is invalid_request, not an empty page", async () => {
      const { services, sessions } = await makeHarness();
      expectError(
        await services.traces(sessions.owner, { job_state: "nope" as never }),
        "invalid_request",
        "unknown job_state",
      );
      expectError(
        await services.traces(sessions.owner, { content: "maybe" as never }),
        "invalid_request",
        "unknown content availability",
      );
      expectError(
        await services.traces(sessions.owner, { trace_mode: "metadata" as never }),
        "invalid_request",
        "trace mode from the superseded vocabulary",
      );
      expectError(
        await services.usage(sessions.owner, { from: "yesterday" }),
        "invalid_request",
        "unparseable from",
      );
      expectError(
        await services.usageSummary(sessions.owner, { from: "2026-09-20T12:00:00.000Z", to: "2026-01-01T00:00:00.000Z" }),
        "invalid_request",
        "reversed range",
      );
    });

    it("a member can read but cannot mutate settings or keys", async () => {
      const { services, sessions, ids } = await makeHarness();
      expectOk(await services.usage(sessions.member, { limit: 5 }), "member usage");
      expectOk(await services.settings.get(sessions.member), "member settings read");
      expectOk(await services.keys.list(sessions.member), "member key list");

      expectError(
        await services.settings.update(sessions.member, { trace_mode: "off" }),
        "forbidden",
        "member settings update",
      );
      expectError(
        await services.keys.create(sessions.member, { name: "member key" }),
        "forbidden",
        "member key creation",
      );
      expectError(await services.keys.revoke(sessions.member, ids.keyId), "forbidden", "member key revocation");
      // R13: evaluation runs are owner and operator only.
      expectError(await services.judgeRuns(sessions.member, {}), "forbidden", "member judge runs");
      expectOk(await services.judgeRuns(sessions.owner, { limit: 5 }), "owner judge runs");
      expectOk(await services.judgeRuns(sessions.operator, { limit: 5 }), "operator judge runs");
    });

    it("a non-operator can neither grant nor see other organizations", async () => {
      const { services, sessions, ids } = await makeHarness();
      expectError(await services.adminOrgs(sessions.owner, {}), "forbidden", "owner admin org list");
      expectError(await services.adminOrgs(sessions.member, {}), "forbidden", "member admin org list");
      expectError(
        await services.adminGrant(sessions.owner, {
          target_org_id: ids.orgId,
          amount: parseMoney("1.00000000"),
          kind: "promotional",
          reason: "self-service grant attempt",
          idempotency_key: "owner-attempt-1",
        }),
        "forbidden",
        "owner grant",
      );

      const orgs = expectOk(await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }), "operator orgs");
      assert.ok(orgs.items.length >= 2, "the operator sees every organization");
      for (const org of orgs.items) {
        assert.ok(isMoney(org.balance.available), "admin org balance");
      }
    });

    it("feedback follows the R3 body, appears immediately, and has provenance the client cannot set", async () => {
      const { services, sessions, ids } = await makeHarness();
      const before = expectOk(
        await services.feedback.list(sessions.owner, ids.availableRequestId),
        "feedback before",
      );

      // A client that sends provenance fields is refused, not quietly trimmed: the fields do not
      // exist in the request, so their presence is a caller bug worth a 400.
      const spoofed = {
        request_id: ids.availableRequestId,
        name: "thumb",
        value: false,
        idempotency_key: "spoofed-provenance-1",
        channel: "api",
        author_role: "judge",
        author_principal: "someone-else@example.com",
        calibration_set: true,
      } as unknown as Parameters<ConsoleServices["feedback"]["submit"]>[1];
      expectError(
        await services.feedback.submit(sessions.owner, spoofed),
        "invalid_request",
        "feedback carrying server-set provenance",
      );

      const entry = expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "rating",
          value: 4,
          comment: "cut off early",
          idempotency_key: "conformance-feedback-1",
        }),
        "feedback submit",
      );
      assert.equal(entry.channel, "console", "the console sets the channel");
      assert.equal(entry.author_role, "customer", "a customer session stays a customer label");
      assert.equal(entry.author_principal, sessions.owner.email, "the principal comes from the session");
      assert.equal(entry.calibration_set, false, "a client cannot enrol its own feedback for calibration");
      assert.ok(inSet(FEEDBACK_CHANNELS, entry.channel) && inSet(AUTHOR_ROLES, entry.author_role));
      assert.ok(inSet(FEEDBACK_NAMES, entry.name), "feedback name");
      assert.equal(entry.value, 4, "the submitted value is kept exactly");
      assert.match(entry.created_at, RFC3339, "feedback created_at");

      // Accepted durably means visible now, whatever the projection is doing.
      const after = expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "feedback after");
      assert.equal(after.length, before.length + 1, "accepted feedback must be listed immediately");
      assert.ok(after.some((item) => item.id === entry.id), "the accepted entry is missing from the list");

      // Each name carries the value type R3 gives it, and nothing else.
      const wrongValues: [string, unknown, string][] = [
        ["thumb", "yes", "a thumb is not a string"],
        ["thumb", 1, "a thumb is not a number"],
        ["rating", 0, "a rating below the range"],
        ["rating", 6, "a rating above the range"],
        ["rating", 3.5, "a fractional rating"],
        ["rating", true, "a boolean rating"],
        ["correction", "", "an empty correction"],
        ["correction", "   ", "a whitespace correction"],
        ["comment", 42, "a numeric comment"],
        ["comment", "x".repeat(MAX_FEEDBACK_TEXT_CHARS + 1), "an oversized comment"],
        ["thumbs_up", true, "a name outside the vocabulary"],
      ];
      for (const [name, value, what] of wrongValues) {
        expectError(
          await services.feedback.submit(sessions.owner, {
            request_id: ids.availableRequestId,
            name,
            value,
            idempotency_key: `bad-${name}-${String(value).slice(0, 12)}`,
          } as unknown as Parameters<ConsoleServices["feedback"]["submit"]>[1]),
          "invalid_request",
          what,
        );
      }
      // Free text is a note, not an upload channel, and not a place to smuggle a structure.
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "correction",
          value: { nested: true } as unknown as string,
          idempotency_key: "conformance-feedback-structure",
        }),
        "invalid_request",
        "non-string correction",
      );
      // R3 requires the key, and 08 §3 bounds it.
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
        } as unknown as Parameters<ConsoleServices["feedback"]["submit"]>[1]),
        "invalid_request",
        "feedback without an idempotency key",
      );
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: "k".repeat(MAX_IDEMPOTENCY_KEY_CHARS + 1),
        }),
        "invalid_request",
        "feedback with an oversized idempotency key",
      );
    });

    it("retention is capped and evaluation consent is a separate control", async () => {
      const { services, sessions } = await makeHarness();
      const initial = expectOk(await services.settings.get(sessions.owner), "settings get");
      assert.ok(inSet(TRACE_MODES, initial.trace_mode), "settings trace_mode");
      assert.ok(
        initial.content_retention_days >= 1 && initial.content_retention_days <= MAX_CONTENT_RETENTION_DAYS,
        "retention within the cap",
      );

      expectError(
        await services.settings.update(sessions.owner, {
          content_retention_days: MAX_CONTENT_RETENTION_DAYS + 1,
        }),
        "invalid_request",
        "retention above the cap",
      );
      expectError(
        await services.settings.update(sessions.owner, { content_retention_days: 0 }),
        "invalid_request",
        "retention of zero days",
      );
      const capped = expectOk(
        await services.settings.update(sessions.owner, {
          content_retention_days: MAX_CONTENT_RETENTION_DAYS,
        }),
        "retention at the cap",
      );
      assert.equal(capped.content_retention_days, MAX_CONTENT_RETENTION_DAYS);

      // Turning capture off does not withdraw consent, and consent does not turn capture on.
      const off = expectOk(await services.settings.update(sessions.owner, { trace_mode: "off" }), "trace mode off");
      assert.equal(off.trace_mode, "off");
      assert.equal(off.evaluation_consent, capped.evaluation_consent, "trace mode must not move consent");

      const toggled = expectOk(
        await services.settings.update(sessions.owner, { evaluation_consent: !off.evaluation_consent }),
        "consent toggle",
      );
      assert.equal(toggled.evaluation_consent, !off.evaluation_consent);
      assert.equal(toggled.trace_mode, "off", "consent must not move trace mode");
      assert.equal(
        toggled.consent_history.length,
        off.consent_history.length + 1,
        "a consent change is audited",
      );
      const latest = toggled.consent_history[toggled.consent_history.length - 1];
      assert.equal(latest.evaluation_consent, toggled.evaluation_consent);
      assert.equal(latest.changed_by, sessions.owner.email);
      // Q16: the exact field set. The stored entry carries `by_operator`, so a body built
      // from it without projecting would leak whether the platform made the change.
      for (const entry of toggled.consent_history) assertConsentHistoryEntry(entry, "consent history");
    });

    it("an operator grant is idempotent per key and conflicts on a changed payload", async () => {
      const { services, sessions, ids } = await makeHarness();
      const input = {
        target_org_id: ids.otherOrgId,
        amount: parseMoney("10.00000000"),
        kind: "promotional" as const,
        reason: "pilot onboarding",
        idempotency_key: "grant-conformance-1",
      };
      const first = expectOk(await services.adminGrant(sessions.operator, input), "first grant");
      assert.equal(first.replayed, false);
      assert.equal(first.org_id, ids.otherOrgId);
      assert.equal(first.operator_principal, sessions.operator.email);
      assert.equal(first.amount, "10.00000000");
      assert.equal(first.balance.ledger_total, "10.00000000", "the grant lands in the wallet");

      const replay = expectOk(await services.adminGrant(sessions.operator, input), "replayed grant");
      assert.equal(replay.grant_id, first.grant_id, "a replay must not create a second grant");
      assert.equal(replay.replayed, true);
      assert.equal(replay.balance.ledger_total, "10.00000000", "a replay must not grant twice");

      expectError(
        await services.adminGrant(sessions.operator, { ...input, amount: parseMoney("11.00000000") }),
        "idempotency_conflict",
        "changed amount under the same key",
      );
      expectError(
        await services.adminGrant(sessions.operator, { ...input, reason: "different reason" }),
        "idempotency_conflict",
        "changed reason under the same key",
      );
      // The target is part of the payload: replaying a key at another organization must not grant
      // a second time, whichever organization the record is filed under.
      expectError(
        await services.adminGrant(sessions.operator, { ...input, target_org_id: ids.orgId }),
        "idempotency_conflict",
        "changed target organization under the same key",
      );
      const ownOrg = expectOk(
        await services.adminGrant(sessions.operator, {
          ...input,
          target_org_id: ids.orgId,
          idempotency_key: "grant-conformance-own-org",
        }),
        "grant to the first organization",
      );
      assert.equal(ownOrg.org_id, ids.orgId);
      assert.notEqual(
        ownOrg.grant_id,
        first.grant_id,
        "two organizations' first grants must not share an id",
      );

      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          amount: parseMoney("0.00000000"),
          idempotency_key: "grant-conformance-zero",
        }),
        "invalid_request",
        "zero grant",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          amount: parseMoney("-1.00000000"),
          idempotency_key: "grant-conformance-negative",
        }),
        "invalid_request",
        "negative grant",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          amount: "1e2" as Money,
          idempotency_key: "grant-conformance-exponent",
        }),
        "invalid_request",
        "exponent amount",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          amount: "1.000000001" as Money,
          idempotency_key: "grant-conformance-over-scale",
        }),
        "invalid_request",
        "an amount with more precision than the scale can hold",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          kind: "purchase" as never,
          idempotency_key: "grant-conformance-kind",
        }),
        "invalid_request",
        "a grant kind outside the free pilot's vocabulary",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          reason: "   ",
          idempotency_key: "grant-conformance-no-reason",
        }),
        "invalid_request",
        "grant without a reason",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          ...input,
          // A well-formed id the harness guarantees exists nowhere — not a literal, which the
          // fixtures could later claim (they did: it became the suspended organization).
          target_org_id: ids.unknownRequestId,
          idempotency_key: "grant-conformance-unknown-org",
        }),
        "not_found",
        "grant to an unknown organization",
      );
    });

    it("every amount the money domain rejects is rejected by the grant boundary too", async () => {
      const { services, sessions, ids } = await makeHarness();
      // The shared case list, fed through the interface rather than to the parser: an implementation
      // with its own amount check — a looser regex, a float parse — would pass the money suite it
      // never calls and fail here.
      const invalid = MONEY_CASES.filter((entry) => !entry.valid).map((entry) => entry.input);
      assert.ok(invalid.length > 40, "the shared case list must supply plenty of rejects");
      let index = 0;
      for (const amount of invalid) {
        index += 1;
        expectError(
          await settle(`grant of ${JSON.stringify(amount)}`, () =>
            services.adminGrant(sessions.operator, {
              target_org_id: ids.orgId,
              amount: amount as Money,
              kind: "promotional",
              reason: "money domain probe",
              idempotency_key: `money-domain-${index}`,
            }),
          ),
          "invalid_request",
          `an amount of ${JSON.stringify(amount)} must be refused`,
        );
      }
      // And the canonical forms the list accepts are accepted, so the check above is not simply
      // refusing everything.
      const valid = MONEY_CASES.filter((entry) => entry.valid && entry.canonical !== null);
      let accepted = 0;
      for (const entry of valid) {
        const canonical = entry.canonical as string;
        if (canonical.startsWith("-") || canonical === "0.00000000") continue;
        accepted += 1;
        expectOk(
          await services.adminGrant(sessions.operator, {
            target_org_id: ids.orgId,
            amount: canonical as Money,
            kind: "promotional",
            reason: "money domain probe",
            idempotency_key: `money-domain-ok-${accepted}`,
          }),
          `an amount of ${canonical} must be accepted`,
        );
        if (accepted >= 3) break;
      }
      assert.ok(accepted >= 3, "at least three positive canonical amounts were accepted");
    });

    it("the provisional input bounds hold", async () => {
      const { services, sessions, ids } = await makeHarness();
      // R17: a key label and an operator reason are bounded, so a form cannot post a document into
      // either. The numbers are provisional; that they are enforced at all is not.
      expectOk(
        await services.keys.create(sessions.owner, { name: "k".repeat(MAX_KEY_NAME_CHARS) }),
        "a key name at the bound",
      );
      expectError(
        await services.keys.create(sessions.owner, { name: "k".repeat(MAX_KEY_NAME_CHARS + 1) }),
        "invalid_request",
        "a key name one character past the bound",
      );
      expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: "1.00000000" as Money,
          kind: "promotional",
          reason: "r".repeat(MAX_GRANT_REASON_CHARS),
          idempotency_key: "bounds-reason-ok",
        }),
        "a reason at the bound",
      );
      expectError(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: "1.00000000" as Money,
          kind: "promotional",
          reason: "r".repeat(MAX_GRANT_REASON_CHARS + 1),
          idempotency_key: "bounds-reason-too-long",
        }),
        "invalid_request",
        "a reason one character past the bound",
      );

      // Entitlement limits: a ceiling, and whole numbers only — a typo must not mean "unlimited".
      expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: null,
          limits: { max_concurrent_requests: MAX_ENTITLEMENT_LIMIT },
          reason: "at the ceiling",
          idempotency_key: "bounds-limit-ok",
        }),
        "a limit at the ceiling",
      );
      for (const [what, value] of [
        ["above the ceiling", MAX_ENTITLEMENT_LIMIT + 1],
        ["fractional", 2.5],
        ["not a number", "many" as unknown as number],
        ["infinite", Number.POSITIVE_INFINITY],
      ] as [string, number][]) {
        expectError(
          await services.adminSetEntitlements(sessions.operator, {
            target_org_id: ids.orgId,
            model_ids: null,
            limits: { max_concurrent_requests: value },
            reason: "bounds",
            idempotency_key: `bounds-limit-${what.replace(/ /g, "-")}`,
          }),
          "invalid_request",
          `a limit that is ${what}`,
        );
      }

      // A second revocation is a conflict and must not move the timestamp of the first.
      const keys = expectOk(await services.keys.list(sessions.owner), "keys");
      const active = keys.find((key) => key.revoked_at === null);
      assert.ok(active !== undefined, "an active key is needed");
      const revoked = expectOk(await services.keys.revoke(sessions.owner, active.id), "first revocation");
      assert.ok(revoked.revoked_at !== null);
      expectError(
        await services.keys.revoke(sessions.owner, active.id),
        "state_conflict",
        "a second revocation is a conflict",
      );
      const listed = expectOk(await services.keys.list(sessions.owner), "keys after").find(
        (key) => key.id === active.id,
      );
      assert.ok(listed !== undefined);
      assert.equal(listed.revoked_at, revoked.revoked_at, "a refused second revocation must not move the timestamp");
    });

    it("content availability decides the payload and never leaks a storage reference", async () => {
      const { services, sessions, ids } = await makeHarness();
      const traces = await walkAll<TraceListItem>(
        (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "traces",
      );
      for (const row of traces) {
        assertTraceDetail(expectOk(await services.traceDetail(sessions.owner, row.request_id), "trace detail"));
        const view = expectOk(await services.traceContent(sessions.owner, row.request_id), "trace content");
        assert.equal(view.request_id, row.request_id);
        assert.equal(view.availability, row.content, "the list and the detail must agree on availability");
        if (view.availability === "available") {
          assert.ok(view.content !== null, "available content must be present");
          assert.equal(view.content.v, 1, "content schema version");
          assert.ok(Array.isArray(view.content.request.messages), "content messages");
          assert.ok(Array.isArray(view.content.response.choices), "content choices");
        } else {
          assert.equal(view.content, null, `${view.availability} content must not be rendered`);
        }
        const serialized = JSON.stringify(view);
        assert.ok(!serialized.includes("s3://"), "no storage path in a content DTO");
        assert.ok(!serialized.includes("X-Amz-"), "no signed URL in a content DTO");
      }

      // R13: the off-mode request is absent from the list above, and is reachable only the way a
      // customer reaches it — from its usage row — where it reports `off` rather than `not_found`.
      assert.ok(
        !traces.some((row) => row.request_id === ids.offRequestId),
        "an off-mode request must not appear in the trace list",
      );
      const usage = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "usage",
      );
      assert.ok(
        usage.some((row) => row.request_id === ids.offRequestId),
        "an off-mode request is still a usage row",
      );
      const offDetail = expectOk(
        await services.traceDetail(sessions.owner, ids.offRequestId),
        "off-mode trace detail",
      );
      assert.equal(offDetail.trace_mode, "off");
      assert.equal(offDetail.content, "off", "an off-mode request reports availability off");
      const offContent = expectOk(
        await services.traceContent(sessions.owner, ids.offRequestId),
        "off-mode trace content",
      );
      assert.equal(offContent.availability, "off");
      assert.equal(offContent.content, null, "an off-mode request has no content to render");
    });

    it("the HTTP status table is the one 08 §3 freezes, code for code", async () => {
      // Parity with the Python `error_codes.json` is a table comparison, so the count is pinned:
      // a code added on one side only shows up here rather than at integration (R22).
      // The values, not just the count: `not_found: 400` or `org_suspended: 401` would pass a
      // count check and mis-render every error page built on the table.
      const expected: Record<string, number> = {
        invalid_request: 400,
        unsupported_parameter: 400,
        unsupported_media: 400,
        media_fetch_failed: 400,
        context_length_exceeded: 400,
        invalid_cursor: 400,
        request_too_large: 413,
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
        capacity_exhausted: 429,
        journal_capacity_exhausted: 429,
        rate_limited: 429,
        internal_error: 500,
        dependency_unavailable: 503,
        deadline_exceeded: 504,
      };
      const served = ERROR_CODES.filter((code) => ERROR_CODE_HTTP_STATUS[code] !== null);
      assert.equal(served.length, 27, "27 codes carry an HTTP status");
      assert.deepEqual(
        Object.fromEntries(served.map((code) => [code, ERROR_CODE_HTTP_STATUS[code]])),
        expected,
        "every served code must carry exactly the status 08 §3 gives it",
      );
      // The three sets partition the union exactly, which is what the G0 parity test compares
      // against the Python `error_codes.json` (R25).
      assert.equal(IN_STREAM_ONLY_CODES.length, 2, "2 codes exist only as in-stream terminal events");
      assert.equal(INTERNAL_ONLY_CODES.length, 8, "8 codes are internal-only domain errors");
      assert.equal(ERROR_CODES.length, 27 + 2 + 8, "the three sets cover the union with no overlap");
      assert.deepEqual(
        [...ERROR_CODES].sort(),
        [...served, ...IN_STREAM_ONLY_CODES, ...INTERNAL_ONLY_CODES].sort(),
        "every code is in exactly one of the three sets",
      );
      for (const code of [...IN_STREAM_ONLY_CODES, ...INTERNAL_ONLY_CODES]) {
        assert.equal(ERROR_CODE_HTTP_STATUS[code], null, `${code} must not carry an HTTP status`);
      }
      // Both a TerminalCause and an internal error: the journal refusing an oversized event.
      assert.ok(INTERNAL_ONLY_CODES.includes("journal_write_failed"), "R25");
      assert.ok(TERMINAL_CAUSES.includes("journal_write_failed"), "and still a terminal cause");
      assert.equal(ERROR_CODE_HTTP_STATUS.upload_expired, 410, "an expired upload window is gone, not a 409");
      assert.equal(ERROR_CODE_HTTP_STATUS.result_expired, 410);
      for (const code of ERROR_CODES) {
        const status = ERROR_CODE_HTTP_STATUS[code];
        assert.ok(status === null || (status >= 400 && status <= 599), `${code} has a nonsense status`);
      }
    });

    it("suspension gates new work and configuration, and nothing else (R33)", async () => {
      const { services, sessions, ids } = await makeHarness();
      const suspended = sessions.suspendedOwner;

      // Every read keeps working. A suspended tenant that cannot see its own usage cannot find out
      // why it was suspended.
      expectOk(await services.usage(suspended, { limit: 5 }), "suspended usage");
      expectOk(await services.usageSummary(suspended, {}), "suspended usage summary");
      expectOk(await services.usageDaily(suspended, {}), "suspended usage daily");
      expectOk(await services.balances(suspended), "suspended balances");
      expectOk(await services.ledger(suspended, { limit: 5 }), "suspended ledger");
      expectOk(await services.traces(suspended, { limit: 5 }), "suspended traces");
      expectOk(await services.settings.get(suspended), "suspended settings read");
      const keys = expectOk(await services.keys.list(suspended), "suspended key list");
      const trace = expectOk(await services.traces(suspended, { limit: 1 }), "a suspended trace").items[0];
      if (trace !== undefined) {
        expectOk(await services.traceDetail(suspended, trace.request_id), "suspended trace detail");
        expectOk(await services.traceContent(suspended, trace.request_id), "suspended trace content");
        expectOk(await services.feedback.list(suspended, trace.request_id), "suspended feedback list");
      }

      // And a leaked key is still revocable: refusing that would make a suspension a security
      // problem rather than a billing one.
      const revocable = keys.find((key) => key.revoked_at === null);
      assert.ok(revocable !== undefined, "the suspended organization needs an active key");
      const revoked = expectOk(await services.keys.revoke(suspended, revocable.id), "suspended key revocation");
      assert.ok(revoked.revoked_at !== null, "the key is revoked while the organization is suspended");

      // New work and new configuration are refused.
      expectError(
        await services.keys.create(suspended, { name: "while suspended" }),
        "org_suspended",
        "suspended key creation",
      );
      expectError(
        await services.settings.update(suspended, { trace_mode: "off" }),
        "org_suspended",
        "suspended settings update",
      );
      expectError(
        await services.feedback.submit(suspended, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: "suspended-feedback",
        }),
        "org_suspended",
        "suspended feedback",
      );
      expectError(await services.judgeRuns(suspended, {}), "org_suspended", "suspended judge runs");

      // The operator still sees it, with its reason and its real wallet.
      const row = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      ).items.find((candidate) => candidate.org_id === ids.suspendedOrgId);
      assert.ok(row !== undefined, "a suspended organization is still listed for the operator");
      assert.equal(row.suspended, true);
      assert.ok(row.suspension_reason !== null && row.suspension_reason.length > 0, "suspension says why");
      assert.ok(isMoney(row.balance.ledger_total), "a suspended organization still has a wallet");
    });

    it("the role and suspension matrix holds for every operation", async () => {
      const { services, sessions, ids } = await makeHarness();
      // Driven from the operation list, so a new operation cannot quietly default to "anyone".
      const probes = operationProbes(services, sessions, ids);
      assertProbesCoverEveryOperation(probes);

      for (const operation of CONSOLE_OPERATIONS) {
        const probe = probes[operation];
        const operatorOnly = (OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation);
        const ownerOnly = (OWNER_ONLY_OPERATIONS as readonly string[]).includes(operation);
        const ownerOrOperator = (OWNER_OR_OPERATOR_OPERATIONS as readonly string[]).includes(operation);

        // A member may read what is not owner-only, and may never write or administer.
        const asMember = await settle(`${operation} as member`, () => probe.call(sessions.member));
        if (operatorOnly || ownerOnly || ownerOrOperator) {
          expectError(asMember, "forbidden", `${operation} must refuse a member`);
        } else {
          expectOk(asMember, `${operation} must allow a member`);
        }

        // An owner without the operator flag may do everything but the operator operations.
        const asOwner = await settle(`${operation} as owner`, () => probe.call(sessions.owner));
        if (operatorOnly) {
          expectError(asOwner, "forbidden", `${operation} must refuse a non-operator owner`);
        } else {
          expectOk(asOwner, `${operation} must allow an owner`);
        }

        // Operator authority is the flag, not the organization role — and it is *only* operator
        // authority. The member-operator must be able to run every operator operation, and must
        // still be refused everything its organization role forbids: a service that treated the
        // flag as a superuser bit would pass a suite that only ever used it on operator operations.
        const asMemberOperator = await settle(`${operation} as member-operator`, () =>
          probe.call(sessions.operatorMember),
        );
        if (operatorOnly) {
          expectOk(asMemberOperator, `${operation} must allow an operator whose org role is member`);
        } else if (ownerOnly) {
          expectError(
            asMemberOperator,
            "forbidden",
            `${operation} is owner-only: the operator flag must not stand in for the role`,
          );
        } else {
          expectOk(asMemberOperator, `${operation} must allow a member-operator to read`);
        }

        // An operator whose *own* organization is suspended still operates the platform: the two
        // are unrelated, and tying them together would mean a suspended operator could not lift the
        // suspension. `operatorMember` belongs to `ids.orgId`, which the case below suspends.
        if (operatorOnly) {
          // Nothing but the probe goes inside `settle`: an assertion made in there would be reported
          // as a thrown error, which reads as a crash rather than as the failure it is.
          expectOk(
            await services.adminSetSuspension(sessions.operator, {
              target_org_id: sessions.operatorMember.orgId,
              suspended: true,
              reason: "matrix: operator's own organization",
              idempotency_key: `matrix-suspend-${operation}`,
            }),
            "suspend the operator's own organization",
          );
          const whileSuspended = await settle(`${operation} with the operator's own org suspended`, () =>
            probe.call(sessions.operatorMember),
          );
          expectOk(
            await services.adminSetSuspension(sessions.operator, {
              target_org_id: sessions.operatorMember.orgId,
              suspended: false,
              reason: "matrix: restore",
              idempotency_key: `matrix-restore-${operation}`,
            }),
            "restore it",
          );
          expectOk(
            whileSuspended,
            `${operation} must work for an operator whose own organization is suspended`,
          );
        }

        // Suspension: refused only where R33 says so, and operator operations keep working (a
        // suspension that could not be lifted would be a trap).
        if (!operatorOnly) {
          const asSuspended = await settle(`${operation} while suspended`, () =>
            probe.call(sessions.suspendedOwner),
          );
          if ((SUSPENDED_REFUSED_OPERATIONS as readonly string[]).includes(operation)) {
            expectError(asSuspended, "org_suspended", `${operation} must refuse a suspended organization`);
          } else if (!probe.tenantIndependent) {
            assert.ok(
              asSuspended.ok || asSuspended.error.code !== "org_suspended",
              `${operation} must not refuse a suspended organization: R33 keeps every read and keys.revoke`,
            );
          }
        }
      }
    });

    it("an operator acts on the organization it names, not on its own (R26)", async () => {
      const { services, sessions, ids } = await makeHarness();
      // Every case here targets the *other* organization, with an operator session that belongs to
      // the first: an implementation that reads `session.orgId` instead of the target passes a suite
      // whose operator happens to own the target, and fails this one.
      assert.notEqual(sessions.operator.orgId, ids.otherOrgId, "the operator must not own the target");

      const grant = expectOk(
        await services.adminGrant(sessions.operatorMember, {
          target_org_id: ids.otherOrgId,
          amount: "3.00000000" as Money,
          kind: "promotional",
          reason: "cross-organization grant",
          idempotency_key: "cross-grant-1",
        }),
        "grant to the other organization",
      );
      assert.equal(grant.org_id, ids.otherOrgId, "the grant must land in the organization it named");
      assert.equal(grant.operator_principal, sessions.operatorMember.email, "and record who made it");
      const otherLedger = expectOk(await services.ledger(sessions.otherOwner, { limit: 5 }), "other ledger");
      const landed = otherLedger.items.find((entry) => entry.id === grant.grant_id);
      assert.ok(landed !== undefined, "the other organization's ledger carries the grant");
      // R41: the tenant learns the platform did it, not which operator. The principal is in the audit.
      assert.equal(landed.actor, PLATFORM_ACTOR, "a customer session sees `platform`, not an operator");
      const ownLedger = expectOk(await services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT }), "own ledger");
      assert.ok(
        !ownLedger.items.some((entry) => entry.id === grant.grant_id),
        "and the operator's own organization did not receive it",
      );

      const suspension = expectOk(
        await services.adminSetSuspension(sessions.operatorMember, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "cross-organization suspension",
          idempotency_key: "cross-suspend-1",
        }),
        "suspend the other organization",
      );
      assert.equal(suspension.org_id, ids.otherOrgId, "the suspension must name its target");
      assert.equal(suspension.suspended, true);
      expectError(await services.keys.create(sessions.otherOwner, { name: "x" }), "org_suspended", "the target is suspended");
      expectOk(await services.usage(sessions.owner, { limit: 1 }), "the operator's own organization is untouched");

      const entitlements = expectOk(
        await services.adminSetEntitlements(sessions.operatorMember, {
          target_org_id: ids.otherOrgId,
          model_ids: [ids.modelId],
          limits: { max_concurrent_requests: 2 },
          reason: "cross-organization entitlement",
          idempotency_key: "cross-entitle-1",
        }),
        "entitle the other organization",
      );
      assert.equal(entitlements.org_id, ids.otherOrgId, "the entitlements must name their target");
      assert.equal(entitlements.updated_by, sessions.operatorMember.email);
      const rows = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      );
      // The same list for either operator: authority is the flag, so an implementation that filtered
      // the list by the caller's own organization would serve a member-operator a shorter one.
      const asMemberOperator = expectOk(
        await services.adminOrgs(sessions.operatorMember, { limit: MAX_PAGE_LIMIT }),
        "org list as a member-operator",
      );
      assert.deepEqual(
        asMemberOperator.items.map((row) => row.org_id),
        rows.items.map((row) => row.org_id),
        "a member-operator must see exactly the same organizations",
      );
      assert.deepEqual(
        asMemberOperator.items.map((row) => row.balance),
        rows.items.map((row) => row.balance),
        "with the same balances",
      );
      const own = rows.items.find((candidate) => candidate.org_id === ids.orgId);
      assert.ok(own !== undefined);
      assert.notDeepEqual(own.entitlements.model_ids, [ids.modelId], "the operator's own org was not entitled");

      // A label on the other organization's request, authored by the operator.
      const label = expectOk(
        await services.calibration.label(sessions.operatorMember, {
          request_id: ids.otherOrgRequestId,
          rubric_version: 2,
          label: "incorrect",
          idempotency_key: "cross-label-1",
        }),
        "label a request of the other organization",
      );
      assert.equal(label.request_id, ids.otherOrgRequestId, "the label must name the request it judged");
      assert.equal(
        label.author_principal,
        sessions.operatorMember.email,
        "the principal is the operator who judged, not the organization's owner",
      );
      assert.notEqual(label.author_principal, sessions.otherOwner.email);
      assert.ok(
        expectOk(await services.calibration.list(sessions.operator, { limit: MAX_PAGE_LIMIT }), "labels").items.some(
          (item) => item.id === label.id,
        ),
        "the label is in the calibration set",
      );
    });

    it("a suspension can be lifted, and lifting it restores exactly what it gated", async () => {
      const { services, sessions, ids } = await makeHarness();
      const keysBefore = expectOk(await services.keys.list(sessions.otherOwner), "their keys");
      const auditFor = async (): Promise<AuditEntry[]> =>
        walkAll<AuditEntry>(
          (cursor) => services.adminAudit(sessions.operator, { target_org_id: ids.otherOrgId, limit: 10, cursor }),
          "audit",
        );
      const auditBefore = await auditFor();

      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "lift probe: suspend",
          idempotency_key: "lift-suspend",
        }),
        "suspend",
      );
      expectError(
        await services.keys.create(sessions.otherOwner, { name: "while suspended" }),
        "org_suspended",
        "creation is gated while suspended",
      );
      // "And nothing else": suspension must not revoke keys or otherwise edit the organization.
      assert.deepEqual(
        expectOk(await services.keys.list(sessions.otherOwner), "their keys while suspended"),
        keysBefore,
        "suspension must not revoke or alter a single key",
      );

      // Accounting is independent of status: an operator may still credit a suspended organization,
      // because a grant is not new work by the tenant.
      const grant = expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.otherOrgId,
          amount: "1.50000000" as Money,
          kind: "promotional",
          reason: "grant to a suspended organization",
          idempotency_key: "lift-grant",
        }),
        "a grant to a suspended organization is allowed",
      );
      // The WalletBalance identity holds on the grant result itself, not only on `balances`.
      assert.equal(
        grant.balance.available,
        subMoney(grant.balance.ledger_total, grant.balance.reserved_total),
        "the grant result's balance must satisfy available = total - reserved",
      );
      const read = expectOk(await services.balances(sessions.otherOwner), "their balance");
      assert.deepEqual(grant.balance, read, "and must agree with what the organization reads");

      // The same identity where holds actually exist, or a result that simply echoed the ledger
      // total would pass: `ids.orgId` is the organization with an outstanding reservation.
      const held = expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: "1.00000000" as Money,
          kind: "promotional",
          reason: "wallet identity with holds",
          idempotency_key: "lift-grant-held",
        }),
        "a grant to an organization with holds",
      );
      assert.equal(compareMoney(held.balance.reserved_total, ZERO_MONEY), 1, "that organization holds something");
      assert.equal(
        held.balance.available,
        subMoney(held.balance.ledger_total, held.balance.reserved_total),
        "the grant result must subtract the holds, not report the ledger total as available",
      );
      assert.deepEqual(
        held.balance,
        expectOk(await services.balances(sessions.owner), "their balance"),
        "and agree with the wallet the organization reads",
      );

      const lifted = expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: false,
          reason: "lift probe: restore",
          idempotency_key: "lift-restore",
        }),
        "restore",
      );
      assert.equal(lifted.suspended, false, "the restore must actually clear the suspension");
      assert.equal(lifted.suspension_reason, "lift probe: restore");
      expectOk(
        await services.keys.create(sessions.otherOwner, { name: "after the restore" }),
        "what suspension gated must work again",
      );
      expectOk(await services.settings.update(sessions.otherOwner, { trace_mode: "minimal" }), "and so must this");

      const auditAfter = await auditFor();
      const appended = auditAfter.length - auditBefore.length;
      assert.equal(appended, 3, "suspend, grant and restore each append one entry");
      const suspensions = auditAfter
        .slice(0, appended)
        .filter((entry) => entry.action === "suspension_set")
        .map((entry) => entry.reason)
        .sort();
      assert.deepEqual(suspensions, ["lift probe: restore", "lift probe: suspend"], "both statuses are recorded");
    });

    it("suspending and restoring an organization with real accounting changes none of it", async () => {
      const { services, sessions, ids } = await makeHarness();
      // `ids.orgId` is the organization with outstanding holds and settled rows; the suspension cases
      // that use the other one cannot detect a suspension that releases a hold or rewrites a
      // settlement, because there is nothing there to rewrite.
      const accounting = async () => {
        const usage = await walkAll<UsageRow>(
          (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
          "usage",
        );
        const ledger = await walkAll<LedgerEntry>(
          (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
          "ledger",
        );
        return {
          usage: usage.map((row) => [row.request_id, row.cost, row.settlement_state, row.max_hold, row.usage_certainty]),
          ledger: ledger.map((entry) => [entry.id, entry.delta, entry.kind, entry.reason, entry.ref, entry.actor]),
          balance: expectOk(await services.balances(sessions.owner), "balance"),
          summary: expectOk(await services.usageSummary(sessions.owner, {}), "summary"),
          adminBalance: expectOk(
            await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
            "operator org list",
          ).items.find((row) => row.org_id === ids.orgId)?.balance,
        };
      };

      const before = await accounting();
      assert.ok(before.usage.some((row) => row[3] !== null), "the organization must have an outstanding hold");
      assert.ok(before.usage.some((row) => row[2] === "settled"), "and settled rows");
      assert.equal(compareMoney(before.balance.reserved_total, ZERO_MONEY), 1, "so its wallet reserves something");

      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: true,
          reason: "accounting invariance: suspend",
          idempotency_key: "accounting-suspend",
        }),
        "suspend",
      );
      assert.deepEqual(await accounting(), before, "suspension must not touch a single accounting row");

      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: false,
          reason: "accounting invariance: restore",
          idempotency_key: "accounting-restore",
        }),
        "restore",
      );
      assert.deepEqual(await accounting(), before, "and neither must lifting it");
    });

    it("entitlement limits are replaced, not merged", async () => {
      const { services, sessions, ids } = await makeHarness();
      expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.otherOrgId,
          model_ids: [ids.modelId],
          limits: { max_concurrent_requests: 4, max_video_seconds: 600 },
          reason: "replace probe: first",
          idempotency_key: "replace-1",
        }),
        "first entitlements",
      );
      const second = expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.otherOrgId,
          model_ids: null,
          limits: { max_requests_per_minute: 30 },
          reason: "replace probe: second",
          idempotency_key: "replace-2",
        }),
        "second entitlements",
      );
      // A write states the whole entitlement, so a limit left out is *removed*, not remembered: a
      // merge would leave a tenant with a limit nobody can see in the request that set it.
      assert.deepEqual(second.limits, { max_requests_per_minute: 30 }, "the limits are what the last write said");
      assert.equal(second.model_ids, null, "and so is the model list");
      const row = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      ).items.find((candidate) => candidate.org_id === ids.otherOrgId);
      assert.ok(row !== undefined);
      assert.deepEqual(row.entitlements.limits, { max_requests_per_minute: 30 }, "as read back");
    });

    it("the operator's own lists page at a small limit too", async () => {
      const { services, sessions, ids } = await makeHarness();
      // Two more labels, so the calibration set is worth walking one row at a time.
      for (const [rubric, requestId, key] of [
        [1, ids.availableRequestId, "page-label-1"],
        [2, ids.otherOrgRequestId, "page-label-2"],
      ] as [number, string, string][]) {
        expectOk(
          await services.calibration.label(sessions.operator, {
            request_id: requestId,
            rubric_version: rubric,
            label: "correct",
            idempotency_key: key,
          }),
          `label ${key}`,
        );
      }
      const labels = await assertSamePagesAtEveryLimit<FeedbackEntry>(
        (limit, cursor) => services.calibration.list(sessions.operator, { limit, cursor }),
        (entry) => entry.id,
        "calibration.list",
      );
      assert.ok(labels.length >= 3, "the harness plus this case give at least three labels");
      const oneByOne = await walkAll<FeedbackEntry>(
        (cursor) => services.calibration.list(sessions.operator, { limit: 1, cursor }),
        "calibration.list at limit 1",
      );
      assert.deepEqual(
        oneByOne.map((entry) => entry.id),
        labels.map((entry) => entry.id),
        "same labels, same order, one row at a time",
      );

      const runs = await assertSamePagesAtEveryLimit<JudgeRun>(
        (limit, cursor) => services.judgeRuns(sessions.owner, { limit, cursor }),
        (run) => run.id,
        "judgeRuns",
      );
      const runsOneByOne = await walkAll<JudgeRun>(
        (cursor) => services.judgeRuns(sessions.owner, { limit: 1, cursor }),
        "judgeRuns at limit 1",
      );
      assert.deepEqual(
        runsOneByOne.map((run) => run.id),
        runs.map((run) => run.id),
        "same runs, same order",
      );

      const audit = await assertSamePagesAtEveryLimit<AuditEntry>(
        (limit, cursor) => services.adminAudit(sessions.operator, { limit, cursor }),
        (entry) => entry.id,
        "adminAudit",
      );
      assert.ok(audit.length >= 2, "the labels above are audited, so there is something to walk");
    });

    it("a fresh read of the ledger puts a new grant in its place", async () => {
      const { services, sessions, ids } = await makeHarness();
      const grant = expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: "7.00000000" as Money,
          kind: "promotional",
          reason: "ordering probe",
          idempotency_key: "ordering-1",
        }),
        "grant",
      );
      const rows = await walkAll<LedgerEntry>(
        (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "ledger after the grant",
      );
      assertStrictTotalOrder(
        rows.map((entry) => ({ at: entry.created_at, id: entry.id })),
        "ledger after a grant",
      );
      const entry = rows.find((candidate) => candidate.id === grant.grant_id);
      assert.ok(entry !== undefined, "the new grant must be in a fresh read");
      assert.equal(entry.created_at, grant.created_at, "with the timestamp the grant reported");
      assert.equal(rows[0].id, grant.grant_id, "and newest first means first");
    });

    it("the three entitlement states are distinct, and the empty one is a denial (R24)", async () => {
      const { services, sessions, ids } = await makeHarness();
      const entitlementsOf = async (orgId: string) => {
        const listed = expectOk(
          await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
          "operator org list",
        );
        const row = listed.items.find((candidate) => candidate.org_id === orgId);
        assert.ok(row !== undefined, `${orgId} must be listed`);
        return row.entitlements;
      };

      // One organization of each kind, because "not configured" and "entitled to nothing" are
      // different facts and a page must not render them the same way.
      const kinds = new Set<string>();
      for (const row of expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      ).items) {
        const list = row.entitlements.model_ids;
        assert.ok(list === null || Array.isArray(list), "model_ids is null or a list, never undefined");
        kinds.add(list === null ? "default" : list.length === 0 ? "none" : "explicit");
        if (list !== null) {
          assert.equal(new Set(list).size, list.length, "a stored entitlement list must not repeat");
        } else {
          assert.equal(row.entitlements.updated_at, null, "an unrecorded default has no audit stamp");
        }
      }
      assert.deepEqual([...kinds].sort(), ["default", "explicit", "none"], "all three states must be present");

      const none = expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [],
          limits: {},
          reason: "conformance: entitle nothing",
          idempotency_key: "entitle-none",
        }),
        "entitle nothing",
      );
      assert.deepEqual(none.model_ids, [], "an empty list is stored as an empty list");
      assert.deepEqual((await entitlementsOf(ids.orgId)).model_ids, [], "and read back as one");

      const back = expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: null,
          limits: {},
          reason: "conformance: restore the default",
          idempotency_key: "entitle-default",
        }),
        "restore the platform default",
      );
      assert.equal(back.model_ids, null, "null is the platform default set, not an empty list");
      assert.equal((await entitlementsOf(ids.orgId)).model_ids, null);

      // `null` and `[]` are different decisions, so the same key cannot mean both.
      expectError(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [],
          limits: {},
          reason: "conformance: restore the default",
          idempotency_key: "entitle-default",
        }),
        "idempotency_conflict",
        "the same key switched between the default and a denial",
      );
      expectError(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [ids.modelId, ids.modelId],
          limits: {},
          reason: "conformance",
          idempotency_key: "entitle-duplicate",
        }),
        "invalid_request",
        "a repeated model id",
      );
      expectError(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: ["model-nobody-serves@2026-01-01"],
          limits: {},
          reason: "conformance",
          idempotency_key: "entitle-unknown-model",
        }),
        "invalid_request",
        "a model the platform does not serve",
      );
      expectError(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: null,
          limits: { unlimited_everything: 1 } as never,
          reason: "conformance",
          idempotency_key: "entitle-bad-limit",
        }),
        "invalid_request",
        "an entitlement limit nobody defined",
      );
      expectError(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: null,
          limits: {},
          reason: "r".repeat(MAX_GRANT_REASON_CHARS + 1),
          idempotency_key: "entitle-long-reason",
        }),
        "invalid_request",
        "an entitlement reason past the bound",
      );

      // A recorded decision is attributed, and the named models come back sorted and intact.
      const explicit = expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [ids.modelId],
          limits: { max_concurrent_requests: 4 },
          reason: "conformance: entitle",
          idempotency_key: "entitle-explicit",
        }),
        "entitle explicitly",
      );
      assert.deepEqual(explicit.model_ids, [ids.modelId]);
      assert.equal(explicit.limits.max_concurrent_requests, 4);
      assert.ok(explicit.updated_at !== null && explicit.updated_by === sessions.operator.email, "the write is audited");
      assert.deepEqual((await entitlementsOf(ids.orgId)).model_ids, [ids.modelId], "the list shows what was set");
      for (const name of ENTITLEMENT_LIMIT_NAMES) {
        assert.equal(typeof name, "string", "the limit vocabulary is a closed list");
      }
    });

    it("an operator suspension needs a reason within the bound", async () => {
      const { services, sessions, ids } = await makeHarness();
      expectError(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "r".repeat(MAX_GRANT_REASON_CHARS + 1),
          idempotency_key: "suspension-long-reason",
        }),
        "invalid_request",
        "a suspension reason past the bound",
      );
      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "r".repeat(MAX_GRANT_REASON_CHARS),
          idempotency_key: "suspension-reason-at-bound",
        }),
        "a suspension reason at the bound",
      );
    });

    it("no customer view ever names an operator (R41)", async () => {
      const { services, sessions, ids } = await makeHarness();
      const operatorPrincipals = [sessions.operator.email, sessions.operatorMember.email];

      // Make the platform act in every way it can, then look everywhere a customer can look.
      expectOk(
        await services.adminGrant(sessions.operatorMember, {
          target_org_id: ids.orgId,
          amount: "5.00000000" as Money,
          kind: "promotional",
          reason: "R41 probe",
          idempotency_key: "r41-grant",
        }),
        "grant",
      );
      expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 1,
          label: "correct",
          comment: "operator-only note",
          idempotency_key: "r41-label",
        }),
        "label",
      );
      expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [ids.modelId],
          limits: {},
          reason: "R41 probe",
          idempotency_key: "r41-entitlements",
        }),
        "entitlements",
      );
      // Ordinary feedback from an operator session: a *customer* signal by R19, but written by an
      // operator, so R41 still applies to who wrote it. Both operator sessions, because either could
      // be the one holding the console open.
      const operatorFeedback = [];
      for (const [session, key] of [
        [sessions.operator, "r41-feedback-1"],
        [sessions.operatorMember, "r41-feedback-2"],
      ] as [SessionContext, string][]) {
        const entry = expectOk(
          await services.feedback.submit(session, {
            request_id: ids.availableRequestId,
            name: "comment",
            value: `operator-submitted note ${key}`,
            idempotency_key: key,
          }),
          `feedback from ${session.email}`,
        );
        assert.equal(entry.author_role, "customer", "an operator's console feedback is a customer signal (R19)");
        operatorFeedback.push({ session, key, id: entry.id });
      }

      // An operator who also owns the organization changing its settings is still the platform.
      expectOk(
        await services.settings.update(sessions.operator, {
          evaluation_consent: !expectOk(await services.settings.get(sessions.owner), "settings").evaluation_consent,
        }),
        "consent change by an operator session",
      );

      const ledger = await walkAll<LedgerEntry>(
        (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "ledger",
      );
      const operatorMade = ledger.filter((entry) => entry.kind === "grant" || entry.kind === "adjustment");
      assert.ok(operatorMade.length > 0, "the harness needs an operator-made ledger entry");
      for (const entry of operatorMade) {
        assert.equal(entry.actor, PLATFORM_ACTOR, `${entry.kind} ${entry.id}: a customer sees the platform, not a person`);
      }
      // Withheld, not lost: an operator reading the same ledger sees who acted, which is what makes
      // the customer-side masking a view rather than missing data. `sessions.operator` owns this
      // organization, so it reads the very rows the owner just read as `platform`.
      const asOperator = expectOk(
        await services.ledger(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "the ledger as an operator reads it",
      );
      const sameGrant = asOperator.items.find((entry) => entry.id === operatorMade[0].id);
      assert.ok(sameGrant !== undefined, "the operator sees the same rows");
      assert.ok(
        sameGrant.actor !== null && sameGrant.actor !== PLATFORM_ACTOR,
        "an operator view must name the operator who made the entry, not `platform`",
      );
      const mine = asOperator.items.find((entry) => entry.ref === "r41-grant");
      assert.ok(mine !== undefined, "including the grant this case just made");
      assert.equal(mine.actor, sessions.operatorMember.email, "and name exactly who made it");
      const settings = expectOk(await services.settings.get(sessions.owner), "settings after");
      const latest = settings.consent_history[settings.consent_history.length - 1];
      assert.ok(latest !== undefined, "the consent change above is recorded");
      for (const change of settings.consent_history) {
        assert.ok(
          !operatorPrincipals.includes(change.changed_by),
          "a consent-history entry must not name an operator",
        );
      }
      assert.equal(latest.changed_by, PLATFORM_ACTOR, "an operator's consent change reads as the platform");
      // Masked, not erased: an operator can still answer "who turned this off?".
      const operatorSettings = expectOk(await services.settings.get(sessions.operator), "settings as an operator");
      const operatorLatest = operatorSettings.consent_history[operatorSettings.consent_history.length - 1];
      assert.ok(operatorLatest !== undefined);
      assert.equal(
        operatorLatest.changed_by,
        sessions.operator.email,
        "an operator view keeps the principal that made the change",
      );
      // A customer's own change is still attributed to the customer.
      const byOwner = expectOk(
        await services.settings.update(sessions.owner, { evaluation_consent: !latest.evaluation_consent }),
        "the owner changing consent",
      );
      const ownerEntry = byOwner.consent_history[byOwner.consent_history.length - 1];
      assert.equal(ownerEntry.changed_by, sessions.owner.email, "an organization's own action keeps its own principal");

      // A *replay* of a settings.update an operator made must mask exactly as a read does.
      // The idempotency scope is (organization, operation, key), so a customer of the same
      // organization can reach the stored operator result by retrying that key: a store
      // that returns its stored row unprojected hands over the operator's address on that
      // one path while masking it everywhere else.
      const q18 = {
        evaluation_consent: !ownerEntry.evaluation_consent,
        idempotency_key: "r41-settings-replay",
      };
      const byOperator = expectOk(
        await services.settings.update(sessions.operator, q18),
        "an operator changing the organization's consent",
      );
      const operatorChange = byOperator.consent_history[byOperator.consent_history.length - 1];
      assert.equal(
        operatorChange.changed_by,
        sessions.operator.email,
        "an operator's own view of its own change names the operator",
      );
      const customerReplay = expectOk(
        await services.settings.update(sessions.owner, q18),
        "the customer replaying the operator's settings key",
      );
      const replayedChange = customerReplay.consent_history[customerReplay.consent_history.length - 1];
      assert.equal(
        replayedChange.changed_at,
        operatorChange.changed_at,
        "the replay returns the original consent entry, not a second one",
      );
      assert.equal(
        replayedChange.changed_by,
        PLATFORM_ACTOR,
        "a replayed settings.update must mask the operator exactly as a read does",
      );
      for (const change of customerReplay.consent_history) {
        assert.ok(
          !operatorPrincipals.includes(change.changed_by),
          "no entry in a replayed settings body may name an operator",
        );
      }

      // The customer's own views of those entries must not name the operator who submitted them, and
      // neither must an *idempotent replay* the customer triggers with the same key.
      for (const { key, id } of operatorFeedback) {
        const listed = expectOk(
          await services.feedback.list(sessions.owner, ids.availableRequestId),
          "feedback list",
        );
        const entry = listed.find((candidate) => candidate.id === id);
        assert.ok(entry !== undefined, "the operator-submitted entry is in the customer's list");
        assert.equal(
          entry.author_principal,
          PLATFORM_ACTOR,
          "a customer must see `platform`, not the operator who submitted it",
        );
        const replay = expectOk(
          await services.feedback.submit(sessions.owner, {
            request_id: ids.availableRequestId,
            name: "comment",
            value: `operator-submitted note ${key}`,
            idempotency_key: key,
          }),
          "the customer replaying the same key",
        );
        assert.equal(replay.id, id, "the replay returns the original entry");
        assert.equal(
          replay.author_principal,
          PLATFORM_ACTOR,
          "and a replay must mask the principal exactly as a read does",
        );
      }

      // An operator reading the same entries still sees who wrote them.
      const asOperatorList = expectOk(
        await services.feedback.list(sessions.operator, ids.availableRequestId),
        "feedback as an operator reads it",
      );
      for (const { session, id } of operatorFeedback) {
        const entry = asOperatorList.find((candidate) => candidate.id === id);
        assert.ok(entry !== undefined);
        assert.equal(entry.author_principal, session.email, "an operator view keeps the principal");
      }

      // Every customer-session read, for both an owner and a member.
      for (const [session, who] of [
        [sessions.owner, "owner"],
        [sessions.member, "member"],
      ] as [SessionContext, string][]) {
        const views: [string, unknown][] = [
          ["usage", await services.usage(session, { limit: MAX_PAGE_LIMIT })],
          ["usageSummary", await services.usageSummary(session, {})],
          ["usageDaily", await services.usageDaily(session, {})],
          ["balances", await services.balances(session)],
          ["ledger", await services.ledger(session, { limit: MAX_PAGE_LIMIT })],
          ["traces", await services.traces(session, { limit: MAX_PAGE_LIMIT })],
          ["traceDetail", await services.traceDetail(session, ids.availableRequestId)],
          ["traceContent", await services.traceContent(session, ids.availableRequestId)],
          ["feedback.list", await services.feedback.list(session, ids.availableRequestId)],
          ["settings.get", await services.settings.get(session)],
          ["keys.list", await services.keys.list(session)],
        ];
        for (const [name, view] of views) {
          const serialized = JSON.stringify(view);
          for (const principal of operatorPrincipals) {
            assert.ok(!serialized.includes(principal), `${who}: ${name} exposed the operator ${principal}`);
          }
          assert.ok(!serialized.includes("operator-only note"), `${who}: ${name} exposed an operator's note`);
        }
      }

      // And the operator's own views keep the identity, or the audit trail would be useless.
      const audit = await walkAll<AuditEntry>(
        (cursor) => services.adminAudit(sessions.operator, { limit: MAX_PAGE_LIMIT, cursor }),
        "audit",
      );
      assert.ok(
        audit.some((entry) => operatorPrincipals.includes(entry.actor_principal)),
        "the audit trail must name the operator who acted",
      );
      assert.ok(
        audit.some((entry) => entry.actor_principal === sessions.operatorMember.email),
        "including the one whose organization role is only member",
      );
    });

    it("an operator write is audited, and a restore adds an entry instead of erasing one (R34)", async () => {
      const { services, sessions, ids } = await makeHarness();
      const auditFor = async (orgId: string): Promise<AuditEntry[]> =>
        walkAll<AuditEntry>(
          (cursor) => services.adminAudit(sessions.operator, { target_org_id: orgId, limit: 10, cursor }),
          "audit",
        );

      // Operator-only whatever the query says — including an owner asking only for its *own*
      // organization's entries, which is the request a naive implementation would allow.
      for (const [session, who] of [
        [sessions.owner, "owner"],
        [sessions.member, "member"],
        [sessions.otherOwner, "another organization's owner"],
        [sessions.suspendedOwner, "a suspended organization's owner"],
      ] as [SessionContext, string][]) {
        for (const [what, query] of [
          ["no filter", {}],
          ["its own organization", { target_org_id: session.orgId }],
          ["another organization", { target_org_id: ids.otherOrgId }],
          ["a page of its own", { target_org_id: session.orgId, limit: 5 }],
        ] as [string, AuditQuery][]) {
          expectError(
            await settle(`${who} reading the audit for ${what}`, () => services.adminAudit(session, query)),
            "forbidden",
            `${who} must not read the audit, even for ${what}`,
          );
        }
      }
      const before = await auditFor(ids.otherOrgId);

      expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.otherOrgId,
          amount: "1.00000000" as Money,
          kind: "promotional",
          reason: "audit: grant",
          idempotency_key: "audit-grant",
        }),
        "grant",
      );
      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "audit: suspend",
          idempotency_key: "audit-suspend",
        }),
        "suspend",
      );
      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: false,
          reason: "audit: restore",
          idempotency_key: "audit-restore",
        }),
        "restore",
      );
      expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.otherOrgId,
          model_ids: [ids.modelId],
          limits: { max_requests_per_minute: 30 },
          reason: "audit: entitle",
          idempotency_key: "audit-entitle",
        }),
        "entitle",
      );
      expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.otherOrgRequestId,
          rubric_version: 1,
          label: "correct",
          idempotency_key: "audit-label",
        }),
        "label",
      );

      const after = await auditFor(ids.otherOrgId);
      assert.equal(after.length, before.length + 5, "each operator write appends exactly one entry");
      const added = after.slice(0, 5);
      for (const entry of added) {
        assert.match(entry.at, RFC3339, "an audit entry is timestamped");
        assert.equal(entry.actor_principal, sessions.operator.email, "and names who did it");
        assert.ok(inSet(AUDIT_ACTIONS, entry.action), `unknown audit action ${entry.action}`);
        assert.equal(entry.target_org_id, ids.otherOrgId, "and which organization it acted on");
        assert.ok(entry.reason.length > 0, "and why");
        assert.ok(entry.idempotency_key.length > 0, "and under which key");
        assert.ok(entry.after !== null && typeof entry.after === "object", "and what it changed to");
      }
      const actions = added.map((entry) => entry.action).sort();
      assert.deepEqual(actions, ["calibration_label", "entitlements_set", "grant", "suspension_set", "suspension_set"]);

      // The restore did not overwrite the suspension: both entries are there, with their own
      // reasons, and the before/after of each says what changed.
      const suspensions = added.filter((entry) => entry.action === "suspension_set");
      assert.equal(suspensions.length, 2, "a restore adds an entry rather than replacing one");
      const reasons = suspensions.map((entry) => entry.reason).sort();
      assert.deepEqual(reasons, ["audit: restore", "audit: suspend"], "each keeps its own reason");
      for (const entry of suspensions) {
        assert.ok(entry.before !== null, "a status change records what it changed from");
        assert.notDeepEqual(entry.before, entry.after, "and that something actually changed");
      }
      const entitled = added.find((entry) => entry.action === "entitlements_set");
      assert.ok(entitled !== undefined);
      assert.equal(entitled.reason, "audit: entitle", "the entitlement reason is kept in the audit");
      assert.ok(entitled.after !== null && typeof entitled.after === "object");

      // A replay is not an event: it appends nothing, and neither does a conflict.
      const afterFive = await auditFor(ids.otherOrgId);
      expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.otherOrgId,
          amount: "1.00000000" as Money,
          kind: "promotional",
          reason: "audit: grant",
          idempotency_key: "audit-grant",
        }),
        "replayed grant",
      );
      expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: true,
          reason: "audit: suspend",
          idempotency_key: "audit-suspend",
        }),
        "replayed suspension",
      );
      expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.otherOrgId,
          model_ids: [ids.modelId],
          limits: { max_requests_per_minute: 30 },
          reason: "audit: entitle",
          idempotency_key: "audit-entitle",
        }),
        "replayed entitlements",
      );
      expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.otherOrgRequestId,
          rubric_version: 1,
          label: "correct",
          idempotency_key: "audit-label",
        }),
        "replayed label",
      );
      assert.deepEqual(await auditFor(ids.otherOrgId), afterFive, "a replay must not append an audit entry");

      expectError(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.otherOrgId,
          amount: "2.00000000" as Money,
          kind: "promotional",
          reason: "audit: grant",
          idempotency_key: "audit-grant",
        }),
        "idempotency_conflict",
        "a changed grant payload",
      );
      expectError(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.otherOrgId,
          suspended: false,
          reason: "audit: suspend",
          idempotency_key: "audit-suspend",
        }),
        "idempotency_conflict",
        "a changed suspension payload",
      );
      assert.deepEqual(await auditFor(ids.otherOrgId), afterFive, "a conflict must not append an audit entry");

      // before/after, pinned for each action rather than only for suspension.
      const grantEntry = added.find((entry) => entry.action === "grant");
      assert.ok(grantEntry !== undefined);
      const balanceNow = expectOk(await services.balances(sessions.otherOwner), "their balance");
      assert.equal(
        grantEntry.after.ledger_total,
        balanceNow.ledger_total,
        "a grant's `after` is the total the grant produced, not the one before it",
      );
      assert.notEqual(grantEntry.before?.ledger_total, grantEntry.after.ledger_total, "and `before` is the earlier total");
      assert.ok(entitled.before !== null, "an entitlement change records what it changed from");
      assert.notDeepEqual(
        entitled.before.model_ids,
        entitled.after.model_ids,
        "and `before` is the previous entitlement, not the new one",
      );
      assert.deepEqual(entitled.after.model_ids, [ids.modelId], "while `after` is what was set");

      // Scoped, ordered and paged like every other list.
      const all = await walkAll<AuditEntry>(
        (cursor) => services.adminAudit(sessions.operator, { limit: 3, cursor }),
        "all audit entries",
      );
      assert.ok(all.length >= after.length, "an unfiltered read includes the filtered one");
      assertStrictTotalOrder(
        all.map((entry) => ({ at: entry.at, id: entry.id })),
        "audit",
      );
      for (const entry of await auditFor(ids.orgId)) {
        assert.equal(entry.target_org_id, ids.orgId, "the filter must filter");
      }
    });

    it("an operator label is the only path to operator authorship and calibration membership", async () => {
      const { services, sessions, ids } = await makeHarness();

      // The same console control, three sessions, one provenance: customer. An operator holding a
      // customer's org open in the console is not labelling anything (02, R19).
      for (const [session, who] of [
        [sessions.owner, "owner"],
        [sessions.member, "member"],
        [sessions.operator, "operator"],
      ] as [SessionContext, string][]) {
        const entry = expectOk(
          await services.feedback.submit(session, {
            request_id: ids.availableRequestId,
            name: "thumb",
            value: true,
            idempotency_key: `provenance-${who}`,
          }),
          `${who} feedback`,
        );
        assert.equal(entry.channel, "console", `${who}: the channel is the console`);
        assert.equal(entry.author_role, "customer", `${who}: console feedback is a customer signal`);
        assert.equal(entry.calibration_set, false, `${who}: submitting does not enrol for calibration`);
        assert.equal(entry.rubric_version, null, `${who}: no rubric is involved`);
        assert.equal(entry.author_principal, session.email);
        assertFeedbackEntry(entry, `${who} feedback`);
      }

      // Only an operator may label, and the label is what carries the operator role.
      expectError(
        await services.calibration.label(sessions.owner, {
          request_id: ids.availableRequestId,
          rubric_version: 3,
          label: "correct",
          idempotency_key: "label-denied-owner",
        }),
        "forbidden",
        "an owner cannot write a calibration label",
      );
      expectError(
        await services.calibration.list(sessions.owner, {}),
        "forbidden",
        "an owner cannot read the calibration set",
      );
      const label = expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 3,
          label: "partially_correct",
          comment: "missed the second vehicle",
          idempotency_key: "label-1",
        }),
        "operator calibration label",
      );
      assert.equal(label.author_role, "operator");
      assert.equal(label.name, "calibration_label");
      assert.equal(label.value, "partially_correct");
      assert.equal(label.calibration_set, true);
      assert.equal(label.rubric_version, 3);
      assertFeedbackEntry(label, "calibration label");

      const replayed = expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 3,
          label: "partially_correct",
          comment: "missed the second vehicle",
          idempotency_key: "label-1",
        }),
        "replayed calibration label",
      );
      assert.equal(replayed.id, label.id, "a replayed label is the same label");
      expectError(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 4,
          label: "partially_correct",
          idempotency_key: "label-1",
        }),
        "idempotency_conflict",
        "the same key against another rubric",
      );
      expectError(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 3,
          label: "excellent" as never,
          idempotency_key: "label-bad-value",
        }),
        "invalid_request",
        "a label outside the vocabulary",
      );
      expectError(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 0,
          label: "correct",
          idempotency_key: "label-bad-rubric",
        }),
        "invalid_request",
        "a rubric version of zero",
      );

      const set = expectOk(await services.calibration.list(sessions.operator, { limit: MAX_PAGE_LIMIT }), "calibration set");
      assert.ok(set.items.some((item) => item.id === label.id), "the label is in the calibration set");
      for (const item of set.items) assertFeedbackEntry(item, "calibration set entry");

      // And every entry the customer can see obeys the same invariant.
      for (const entry of expectOk(
        await services.feedback.list(sessions.owner, ids.availableRequestId),
        "feedback list",
      )) {
        assertFeedbackEntry(entry, "listed feedback");
      }
    });

    it("a created key's secret is shown once and never again, to anybody", async () => {
      const { services, sessions } = await makeHarness();
      const created = expectOk(
        await services.keys.create(sessions.owner, { name: "secret once", idempotency_key: "secret-1" }),
        "key create",
      );
      assert.equal(created.replayed, false);
      assert.ok(typeof created.secret === "string" && created.secret.length > 0, "the first response carries it");
      const secret = created.secret;

      // Same user, same key: metadata only (R16).
      const again = expectOk(
        await services.keys.create(sessions.owner, { name: "secret once", idempotency_key: "secret-1" }),
        "replayed key create",
      );
      assert.equal(again.id, created.id, "a replay is the same key");
      assert.equal(again.secret, null, "a replay must not carry the secret");
      assert.equal(again.replayed, true);

      // Another session of the same organization replaying the key must not receive it either.
      const byOperator = expectOk(
        await services.keys.create(sessions.operator, { name: "secret once", idempotency_key: "secret-1" }),
        "replay from another session",
      );
      assert.equal(byOperator.id, created.id, "the record is the organization's, not the user's");
      assert.equal(byOperator.secret, null, "a different user must never receive the secret");
      assert.equal(byOperator.replayed, true);

      // Revoked, and still no secret — the replay reads the key's *current* metadata.
      expectOk(await services.keys.revoke(sessions.owner, created.id), "revoke");
      const afterRevoke = expectOk(
        await services.keys.create(sessions.owner, { name: "secret once", idempotency_key: "secret-1" }),
        "replay after revoke",
      );
      assert.equal(afterRevoke.secret, null, "a revoked key certainly has no secret to show");
      assert.ok(afterRevoke.revoked_at !== null, "the replay shows the revocation");

      // Exhaustive read sweep: the secret must appear in no response of any operation, for the
      // owner or for the operator. This is the portable half of "it is stored nowhere".
      const responses: unknown[] = [
        await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }),
        await services.usageSummary(sessions.owner, {}),
        await services.usageDaily(sessions.owner, {}),
        await services.balances(sessions.owner),
        await services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT }),
        await services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT }),
        await services.settings.get(sessions.owner),
        await services.keys.list(sessions.owner),
        await services.judgeRuns(sessions.owner, { limit: MAX_PAGE_LIMIT }),
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        await services.calibration.list(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        again,
        byOperator,
        afterRevoke,
      ];
      for (const response of responses) {
        assert.ok(
          !JSON.stringify(response).includes(secret),
          "a key secret leaked into a later response",
        );
      }
    });

    it("judge runs separate estimates, limited evaluations and held budgets", async () => {
      const { services, sessions } = await makeHarness();
      const runs = await walkAll<JudgeRun>(
        (cursor) => services.judgeRuns(sessions.owner, { limit: 20, cursor }),
        "judge runs",
      );
      for (const run of runs) assertJudgeRun(run);
    });
  });

  runMutationSafetyConformance(makeHarness, label);
}

/**
 * The defect class every review round found a fresh instance of: a mutation that validates *after*
 * it writes. A refused operation must leave the state it touched byte-identical, so the retry that
 * always follows a refusal has exactly one effect — and no reachable input may throw instead of
 * returning a `Result`.
 *
 * Exported separately so C can run it on its own while wiring the real services, and so a
 * regression here names this suite rather than hiding inside the main one.
 */
export function runMutationSafetyConformance(
  makeHarness: ConsoleHarnessFactory,
  label = "ConsoleServices",
): void {
  describe(`${label}: mutations validate before they write`, () => {
    it("an amount that would leave the money domain is refused, and nothing is written", async () => {
      const { services, sessions, ids } = await makeHarness();
      const ledgerIds = async (): Promise<string[]> =>
        (
          await walkAll<LedgerEntry>(
            (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
            "ledger",
          )
        ).map((entry) => entry.id);

      // The largest representable amount: one of these two grants must overflow the wallet total,
      // whatever the organization already holds. The type-valid input is the whole point — it
      // passes every field check and only the *prospective total* refuses it.
      const first = await settle("largest grant", () =>
        services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: LARGEST_MONEY,
          kind: "promotional",
          reason: "money domain probe",
          idempotency_key: "domain-probe-1",
        }),
      );
      const beforeIds = await ledgerIds();

      for (const attempt of [1, 2]) {
        const overflowing = await settle(`overflowing grant, attempt ${attempt}`, () =>
          services.adminGrant(sessions.operator, {
            target_org_id: ids.orgId,
            amount: LARGEST_MONEY,
            kind: "promotional",
            reason: "money domain probe",
            idempotency_key: "domain-probe-2",
          }),
        );
        expectError(overflowing, "invalid_request", `overflowing grant, attempt ${attempt}`);
        // A retry under the same key must behave identically: the refused attempt filed no
        // idempotency record, so this is still the first (and still refused) grant.
        assert.deepEqual(await ledgerIds(), beforeIds, `overflow attempt ${attempt} changed the ledger`);
      }

      // The reads a console page makes right after a refusal must still work. Before the fix, the
      // committed-then-refused entry made every balance read throw for the rest of the session.
      const balance = expectOk(await settle("balances after overflow", () => services.balances(sessions.owner)), "balances");
      assert.ok(isMoney(balance.ledger_total) && isMoney(balance.available), "balances stay canonical money");
      assert.equal(balance.available, subMoney(balance.ledger_total, balance.reserved_total));
      expectOk(
        await settle("admin orgs after overflow", () => services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT })),
        "adminOrgs after overflow",
      );
      expectOk(
        await settle("ledger after overflow", () => services.ledger(sessions.owner, { limit: 5 })),
        "ledger after overflow",
      );
      if (first.ok) {
        assert.equal(first.value.amount, LARGEST_MONEY, "an accepted grant keeps its amount");
      }
    });

    it("every mutating operation validates before it writes, whichever field is bad", async () => {
      const { services, sessions, ids } = await makeHarness();
      // The whole readable state, so "nothing changed" is a deep comparison rather than a spot check.
      const snapshot = async () => ({
        ledger: (
          await walkAll<LedgerEntry>(
            (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
            "ledger",
          )
        ).map((entry) => [entry.id, entry.delta, entry.kind, entry.reason, entry.ref, entry.actor]),
        usage: (
          await walkAll<UsageRow>(
            (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
            "usage",
          )
        ).map((row) => [row.request_id, row.cost, row.settlement_state, row.max_hold]),
        keys: expectOk(await services.keys.list(sessions.owner), "keys"),
        settings: expectOk(await services.settings.get(sessions.owner), "settings"),
        feedback: expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "feedback"),
        balance: expectOk(await services.balances(sessions.owner), "balance"),
        orgs: expectOk(await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }), "orgs").items,
        labels: expectOk(await services.calibration.list(sessions.operator, { limit: MAX_PAGE_LIMIT }), "labels").items,
        audit: await walkAll<AuditEntry>(
          (cursor) => services.adminAudit(sessions.operator, { limit: MAX_PAGE_LIMIT, cursor }),
          "audit",
        ),
        otherSettings: expectOk(await services.settings.get(sessions.otherOwner), "their settings"),
        otherKeys: expectOk(await services.keys.list(sessions.otherOwner), "their keys"),
      });

      // Each refusal below has *valid earlier fields and one invalid later field*: an implementation
      // that applies fields as it validates them leaves the earlier ones written.
      const refusals: [string, () => Promise<Result<unknown>>, string][] = [
        [
          "adminGrant: valid target and kind, bad amount",
          () =>
            services.adminGrant(sessions.operator, {
              target_org_id: ids.orgId,
              amount: "1e2" as Money,
              kind: "promotional",
              reason: "refusal drill",
              idempotency_key: "refusal-grant",
            }),
          "invalid_request",
        ],
        [
          "adminGrant: everything valid but the reason",
          () =>
            services.adminGrant(sessions.operator, {
              target_org_id: ids.orgId,
              amount: "1.00000000" as Money,
              kind: "promotional",
              reason: "   ",
              idempotency_key: "refusal-grant-reason",
            }),
          "invalid_request",
        ],
        [
          "feedback.submit: valid name, value out of range",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "rating",
              value: 9,
              idempotency_key: "refusal-feedback",
            }),
          "invalid_request",
        ],
        [
          "feedback.submit: valid name and value, empty comment",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "thumb",
              value: true,
              comment: "   ",
              idempotency_key: "refusal-feedback-comment",
            }),
          "invalid_request",
        ],
        [
          "settings.update: valid trace_mode, bad retention",
          () =>
            services.settings.update(sessions.owner, {
              trace_mode: "minimal",
              content_retention_days: MAX_CONTENT_RETENTION_DAYS + 1,
            }),
          "invalid_request",
        ],
        [
          "settings.update: valid trace_mode and retention, bad consent",
          () =>
            services.settings.update(sessions.owner, {
              trace_mode: "off",
              content_retention_days: 7,
              evaluation_consent: "yes" as never,
            }),
          "invalid_request",
        ],
        [
          "keys.create: valid name, bad trace_mode",
          () => services.keys.create(sessions.owner, { name: "later field bad", trace_mode: "metadata" as never }),
          "invalid_request",
        ],
        [
          "keys.create: blank name",
          () => services.keys.create(sessions.owner, { name: "  " }),
          "invalid_request",
        ],
        [
          "keys.revoke: unknown key",
          () => services.keys.revoke(sessions.owner, ids.unknownRequestId),
          "not_found",
        ],
        [
          "adminSetSuspension: valid target and state, blank reason",
          () =>
            services.adminSetSuspension(sessions.operator, {
              target_org_id: ids.orgId,
              suspended: true,
              reason: "   ",
              idempotency_key: "refusal-suspension",
            }),
          "invalid_request",
        ],
        [
          "adminSetSuspension: valid target and reason, non-boolean state",
          () =>
            services.adminSetSuspension(sessions.operator, {
              target_org_id: ids.orgId,
              suspended: "yes" as never,
              reason: "refusal drill",
              idempotency_key: "refusal-suspension-state",
            }),
          "invalid_request",
        ],
        [
          "adminSetEntitlements: valid models, bad limit value",
          () =>
            services.adminSetEntitlements(sessions.operator, {
              target_org_id: ids.orgId,
              model_ids: [ids.modelId],
              limits: { max_concurrent_requests: -1 },
              reason: "refusal drill",
              idempotency_key: "refusal-entitlements",
            }),
          "invalid_request",
        ],
        [
          "adminSetEntitlements: valid models and limits, blank reason",
          () =>
            services.adminSetEntitlements(sessions.operator, {
              target_org_id: ids.orgId,
              model_ids: [ids.modelId],
              limits: { max_concurrent_requests: 2 },
              reason: "",
              idempotency_key: "refusal-entitlements-reason",
            }),
          "invalid_request",
        ],
        [
          "calibration.label: valid request and label, bad rubric",
          () =>
            services.calibration.label(sessions.operator, {
              request_id: ids.availableRequestId,
              rubric_version: 0,
              label: "correct",
              idempotency_key: "refusal-label",
            }),
          "invalid_request",
        ],
        [
          "calibration.label: valid request and rubric, label outside the vocabulary",
          () =>
            services.calibration.label(sessions.operator, {
              request_id: ids.availableRequestId,
              rubric_version: 2,
              label: "excellent" as never,
              idempotency_key: "refusal-label-value",
            }),
          "invalid_request",
        ],
      ];

      // Every mutating operation must appear, so a new one is not left out.
      const covered = new Set(refusals.map(([what]) => what.split(":")[0]));
      assert.deepEqual(
        [...covered].sort(),
        [...MUTATING_OPERATIONS].sort(),
        "every mutating operation needs a validate-before-write refusal",
      );

      for (const [what, call, code] of refusals) {
        const before = await snapshot();
        expectError(await settle(what, call), code, what);
        assert.deepEqual(await snapshot(), before, `${what}: a refused mutation changed the state anyway`);
      }
    });

    it("an idempotency key is scoped to its operation, its organization and its payload", async () => {
      const { services, sessions, ids } = await makeHarness();
      const key = "scope-probe-1";

      // Same key, same payload: one effect, and the replay says so.
      const grant = {
        target_org_id: ids.orgId,
        amount: "1.00000000" as Money,
        kind: "promotional" as const,
        reason: "idempotency scope",
        idempotency_key: key,
      };
      const first = expectOk(await services.adminGrant(sessions.operator, grant), "first grant");
      const replay = expectOk(await services.adminGrant(sessions.operator, grant), "replayed grant");
      assert.equal(replay.grant_id, first.grant_id, "a replay returns the original grant");
      assert.equal(replay.replayed, true);
      assert.equal(replay.balance.ledger_total, first.balance.ledger_total, "a replay does not grant twice");

      // The same key on a *different operation* is a different record, not a conflict.
      const feedback = expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: key,
        }),
        "feedback under the grant's key",
      );
      const feedbackReplay = expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: key,
        }),
        "replayed feedback",
      );
      assert.equal(feedbackReplay.id, feedback.id, "a replayed submission returns the original entry");
      const entries = expectOk(
        await services.feedback.list(sessions.owner, ids.availableRequestId),
        "feedback after replay",
      );
      assert.equal(
        entries.filter((item) => item.id === feedback.id).length,
        1,
        "a replayed submission must not be recorded twice",
      );

      // A key on an optional-key operation still replays instead of creating a second key.
      const created = expectOk(
        await services.keys.create(sessions.owner, { name: "idempotent key", idempotency_key: key }),
        "key create",
      );
      const createdAgain = expectOk(
        await services.keys.create(sessions.owner, { name: "idempotent key", idempotency_key: key }),
        "replayed key create",
      );
      assert.equal(createdAgain.id, created.id, "a replayed creation returns the first key");
      const keys = expectOk(await services.keys.list(sessions.owner), "keys after replay");
      assert.equal(
        keys.filter((candidate) => candidate.name === "idempotent key").length,
        1,
        "a replayed creation must not mint a second key",
      );

      // …and revoking under a key replays too, rather than conflicting with itself.
      const revocable = keys.find((candidate) => candidate.revoked_at === null);
      assert.ok(revocable !== undefined, "an active key is needed");
      const revoked = expectOk(await services.keys.revoke(sessions.owner, revocable.id, key), "revoke");
      const revokedAgain = expectOk(
        await services.keys.revoke(sessions.owner, revocable.id, key),
        "replayed revoke",
      );
      assert.deepEqual(revokedAgain, revoked, "a replayed revocation returns the original result");
      expectError(
        await services.keys.revoke(sessions.owner, revocable.id, "a-different-key"),
        "state_conflict",
        "and without the original key it is simply already revoked",
      );

      // The same key from another organization is a different record: no collision, no conflict.
      const theirSettings = expectOk(await services.settings.get(sessions.otherOwner), "their settings");
      const ours = expectOk(
        await services.settings.update(sessions.owner, { trace_mode: "minimal", idempotency_key: "shared-key" }),
        "our settings write under a shared key",
      );
      const theirs = expectOk(
        await services.settings.update(sessions.otherOwner, {
          trace_mode: theirSettings.trace_mode === "full" ? "minimal" : "full",
          idempotency_key: "shared-key",
        }),
        "their settings write under the same key",
      );
      assert.equal(ours.trace_mode, "minimal", "our write stands");
      assert.notEqual(
        theirs.trace_mode,
        theirSettings.trace_mode,
        "their write under the same key must apply, not replay ours",
      );
      const sharedFeedback = expectOk(
        await services.feedback.submit(sessions.otherOwner, {
          request_id: ids.otherOrgRequestId,
          name: "thumb",
          value: false,
          idempotency_key: key,
        }),
        "their feedback under our key",
      );
      assert.notEqual(sharedFeedback.id, feedback.id, "two organizations' records must not share a key");

      // One field changed at a time, for every field of every payload: a conflict check that
      // compared only *some* fields survives a payload that omits one of them.
      const conflicts: [string, () => Promise<Result<unknown>>][] = [
        ["adminGrant target_org_id", () => services.adminGrant(sessions.operator, { ...grant, target_org_id: ids.otherOrgId })],
        ["adminGrant amount", () => services.adminGrant(sessions.operator, { ...grant, amount: "2.00000000" as Money })],
        ["adminGrant reason", () => services.adminGrant(sessions.operator, { ...grant, reason: "another reason" })],
        [
          "feedback.submit request_id",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.offRequestId,
              name: "thumb",
              value: true,
              idempotency_key: key,
            }),
        ],
        [
          "feedback.submit name",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "rating",
              value: 3,
              idempotency_key: key,
            }),
        ],
        [
          "feedback.submit value",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "thumb",
              value: false,
              idempotency_key: key,
            }),
        ],
        [
          "feedback.submit comment",
          () =>
            services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "thumb",
              value: true,
              comment: "now with a comment",
              idempotency_key: key,
            }),
        ],
        ["keys.create name", () => services.keys.create(sessions.owner, { name: "another name", idempotency_key: key })],
        [
          "keys.create trace_mode",
          () => services.keys.create(sessions.owner, { name: "idempotent key", trace_mode: "off", idempotency_key: key }),
        ],
        [
          "keys.revoke key_id",
          async () => {
            const active = expectOk(await services.keys.list(sessions.owner), "keys").find(
              (candidate) => candidate.revoked_at === null,
            );
            assert.ok(active !== undefined, "another active key is needed");
            return services.keys.revoke(sessions.owner, active.id, key);
          },
        ],
        [
          "settings.update trace_mode",
          () => services.settings.update(sessions.owner, { trace_mode: "off", idempotency_key: "shared-key" }),
        ],
        [
          "settings.update content_retention_days",
          () =>
            services.settings.update(sessions.owner, {
              trace_mode: "minimal",
              content_retention_days: 5,
              idempotency_key: "shared-key",
            }),
        ],
        [
          "settings.update evaluation_consent",
          () =>
            services.settings.update(sessions.owner, {
              trace_mode: "minimal",
              evaluation_consent: !ours.evaluation_consent,
              idempotency_key: "shared-key",
            }),
        ],
      ];
      for (const [what, call] of conflicts) {
        expectError(await settle(what, call), "idempotency_conflict", `${what} changed alone must conflict`);
      }

      // The operator writes, each field alone.
      const suspension = {
        target_org_id: ids.otherOrgId,
        suspended: true,
        reason: "scope probe",
        idempotency_key: "scope-suspension",
      };
      expectOk(await services.adminSetSuspension(sessions.operator, suspension), "suspension");
      for (const [what, changed] of [
        ["target_org_id", { ...suspension, target_org_id: ids.orgId }],
        ["suspended", { ...suspension, suspended: false }],
        ["reason", { ...suspension, reason: "a different reason" }],
      ] as [string, typeof suspension][]) {
        expectError(
          await settle(`adminSetSuspension ${what}`, () => services.adminSetSuspension(sessions.operator, changed)),
          "idempotency_conflict",
          `adminSetSuspension ${what} changed alone must conflict`,
        );
      }
      expectOk(
        await services.adminSetSuspension(sessions.operator, { ...suspension, suspended: false, idempotency_key: "scope-restore" }),
        "restore",
      );

      const entitlements = {
        target_org_id: ids.otherOrgId,
        model_ids: [ids.modelId],
        limits: { max_concurrent_requests: 3 },
        reason: "scope probe",
        idempotency_key: "scope-entitlements",
      };
      expectOk(await services.adminSetEntitlements(sessions.operator, entitlements), "entitlements");
      for (const [what, changed] of [
        ["target_org_id", { ...entitlements, target_org_id: ids.orgId }],
        ["model_ids", { ...entitlements, model_ids: null }],
        ["limits", { ...entitlements, limits: { max_concurrent_requests: 4 } }],
        ["limits (a different name)", { ...entitlements, limits: { max_requests_per_minute: 3 } }],
        ["reason", { ...entitlements, reason: "a different reason" }],
      ] as [string, typeof entitlements][]) {
        expectError(
          await settle(`adminSetEntitlements ${what}`, () => services.adminSetEntitlements(sessions.operator, changed)),
          "idempotency_conflict",
          `adminSetEntitlements ${what} changed alone must conflict`,
        );
      }

      const label = {
        request_id: ids.availableRequestId,
        rubric_version: 3,
        label: "correct" as const,
        comment: "scope probe",
        idempotency_key: "scope-label",
      };
      expectOk(await services.calibration.label(sessions.operator, label), "label");
      for (const [what, changed] of [
        ["request_id", { ...label, request_id: ids.offRequestId }],
        ["rubric_version", { ...label, rubric_version: 4 }],
        ["label", { ...label, label: "incorrect" as const }],
        ["comment", { ...label, comment: "a different note" }],
      ] as [string, typeof label][]) {
        expectError(
          await settle(`calibration.label ${what}`, () => services.calibration.label(sessions.operator, changed)),
          "idempotency_conflict",
          `calibration.label ${what} changed alone must conflict`,
        );
      }
    });

    it("generated identifiers never collide, within an organization or across two", async () => {
      const { services, sessions, ids } = await makeHarness();
      const seen = new Set<string>();
      const add = (id: string, what: string): void => {
        assert.ok(!seen.has(id), `${what}: ${id} was issued twice`);
        seen.add(id);
      };

      for (const entry of expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "seeded feedback")) {
        add(entry.id, "seeded feedback");
      }
      // Both organizations, enough times that a counter without the tenant in it must collide.
      const minted: FeedbackEntry[] = [];
      for (const [session, requestId, who] of [
        [sessions.owner, ids.availableRequestId, "first organization"],
        [sessions.otherOwner, ids.otherOrgRequestId, "second organization"],
      ] as [SessionContext, string, string][]) {
        for (let i = 0; i < 12; i += 1) {
          minted.push(
            expectOk(
              await services.feedback.submit(session, {
                request_id: requestId,
                name: "comment",
                value: `note ${i}`,
                idempotency_key: `id-probe-feedback-${who}-${i}`,
              }),
              `${who} feedback ${i}`,
            ),
          );
        }
      }
      for (const entry of minted) add(entry.id, "minted feedback");

      for (const [session, who] of [
        [sessions.owner, "first organization"],
        [sessions.otherOwner, "second organization"],
      ] as [SessionContext, string][]) {
        for (let i = 0; i < 3; i += 1) {
          add(expectOk(await services.keys.create(session, { name: `probe ${i}` }), `${who} key ${i}`).id, `${who} key`);
        }
      }
      for (const [session, who] of [
        [sessions.owner, "first organization"],
        [sessions.otherOwner, "second organization"],
      ] as [SessionContext, string][]) {
        const grant = expectOk(
          await services.adminGrant(sessions.operator, {
            target_org_id: session.orgId,
            amount: "3.00000000" as Money,
            kind: "promotional",
            reason: "id collision probe",
            idempotency_key: `id-probe-grant-${session.orgId}`,
          }),
          `${who} grant`,
        );
        add(grant.grant_id, `${who} grant`);
      }
    });

    it("a list walked while rows arrive at its head still returns every row exactly once", async () => {
      const { services, sessions, ids } = await makeHarness();
      const snapshot = (
        await walkAll<LedgerEntry>(
          (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
          "ledger snapshot",
        )
      ).map((entry) => entry.id);
      assert.ok(snapshot.length >= 3, "the harness needs a few ledger rows for this");

      const collected: string[] = [];
      let cursor: string | null = null;
      let inserted = 0;
      for (let page = 0; page < 500; page += 1) {
        const rows: Page<LedgerEntry> = expectOk(
          await services.ledger(sessions.owner, { limit: 2, cursor }),
          `ledger page ${page}`,
        );
        collected.push(...rows.items.map((entry) => entry.id));
        // A grant lands at the head of the ledger between two pages: the classic offset-cursor
        // trap, where the next page repeats a row and the walk loses the last one.
        if (inserted < 2) {
          inserted += 1;
          expectOk(
            await services.adminGrant(sessions.operator, {
              target_org_id: ids.orgId,
              amount: "0.50000000" as Money,
              kind: "promotional",
              reason: "insertion during a walk",
              idempotency_key: `walk-insert-${inserted}`,
            }),
            `grant during the walk ${inserted}`,
          );
        }
        if (rows.next_cursor === null) break;
        cursor = rows.next_cursor;
      }
      assert.equal(new Set(collected).size, collected.length, "a row was returned twice under insertion");
      assert.deepEqual(
        collected,
        snapshot,
        "a walk under head insertion must return the rows that existed when it started, in order",
      );
    });

    it("a filtered walk survives the cursor's own row leaving the filter, and stays complete", async () => {
      const { services, sessions } = await makeHarness();
      const query = { limit: 5, has_feedback: false } as const;
      // The full filtered set up front, so a walk that quietly stops early is a failure rather than
      // a shorter list nobody compared against anything.
      const expected = (
        await walkAll<TraceListItem>(
          (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, has_feedback: false }),
          "the filtered set",
        )
      ).map((row) => row.request_id);
      assert.ok(expected.length > 10, "the harness needs a filtered set worth walking");

      const first = expectOk(await services.traces(sessions.owner, query), "first filtered page");
      assert.ok(first.next_cursor !== null, "the harness needs more than one page of unannotated traces");
      assert.ok(first.items.length > 0);
      const cursorRow = first.items[first.items.length - 1];

      // Annotate exactly the row the cursor points at: it now has feedback, so it no longer matches
      // `has_feedback: false`. A cursor that resumes by *looking up* its row either 400s or — worse —
      // returns an empty page with a null cursor, which a "no duplicates" check would accept.
      expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: cursorRow.request_id,
          name: "thumb",
          value: true,
          idempotency_key: "filter-escape-1",
        }),
        "feedback on the cursor row",
      );

      const second = expectOk(
        await services.traces(sessions.owner, { ...query, cursor: first.next_cursor }),
        "the page after the vanished cursor row",
      );
      assert.ok(second.items.length > 0, "the page after the vanished cursor row must not be empty");

      const collected = [...first.items.map((row) => row.request_id), ...second.items.map((row) => row.request_id)];
      let cursor = second.next_cursor;
      for (let page = 0; page < 500 && cursor !== null; page += 1) {
        const next = expectOk(await services.traces(sessions.owner, { ...query, cursor }), `page ${page}`);
        assert.ok(next.items.length > 0, "a non-final page must not be empty");
        collected.push(...next.items.map((row) => row.request_id));
        cursor = next.next_cursor;
      }
      assert.equal(cursor, null, "the walk must terminate");
      assert.equal(new Set(collected).size, collected.length, "a row was repeated after the cursor row left the filter");
      // Exactly the rows that were in the set when the walk began: the row that left the filter had
      // already been delivered on the first page, so a complete walk loses nothing at all.
      assert.deepEqual(collected, expected, "the walk must cover the whole filtered set, in order");
      for (const row of second.items) {
        assert.equal(row.feedback_count, 0, "the filter still holds for the rows that follow");
      }
    });

    it("nothing creates a legacy purchase entry", async () => {
      const { services, sessions, ids } = await makeHarness();
      const purchases = async (): Promise<string[]> =>
        (
          await walkAll<LedgerEntry>(
            (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
            "ledger",
          )
        )
          .filter((entry) => entry.kind === "purchase")
          .map((entry) => entry.id);

      const before = await purchases();
      const grant = expectOk(
        await services.adminGrant(sessions.operator, {
          target_org_id: ids.orgId,
          amount: "4.00000000" as Money,
          kind: "promotional",
          reason: "purchase check",
          idempotency_key: "purchase-check-1",
        }),
        "grant",
      );
      assert.ok(grant.grant_id.length > 0);
      const entries = await walkAll<LedgerEntry>(
        (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "ledger after the grant",
      );
      const created = entries.find((entry) => entry.id === grant.grant_id);
      assert.ok(created !== undefined, "the grant must be in the ledger");
      assert.ok(
        (CREATABLE_LEDGER_ENTRY_KINDS as readonly string[]).includes(created.kind),
        `a new entry must not be a legacy kind, got ${created.kind}`,
      );
      assert.deepEqual(await purchases(), before, "R13: nothing creates a purchase entry");
    });

    it("a calibration label is operator data a customer never sees (R35)", async () => {
      const { services, sessions, ids } = await makeHarness();
      const operatorPrincipals = [sessions.operator.email, sessions.operatorMember.email];

      /** Every view a customer has of one request, plus the two list-level signals. */
      const customerViews = async (requestId: string) => ({
        feedback: expectOk(await services.feedback.list(sessions.owner, requestId), "feedback"),
        memberFeedback: expectOk(await services.feedback.list(sessions.member, requestId), "member feedback"),
        detail: expectOk(await services.traceDetail(sessions.owner, requestId), "detail"),
        memberDetail: expectOk(await services.traceDetail(sessions.member, requestId), "member detail"),
        content: expectOk(await services.traceContent(sessions.owner, requestId), "content"),
        annotated: (
          await walkAll<TraceListItem>(
            (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, has_feedback: true }),
            "annotated traces",
          )
        ).map((row) => row.request_id),
        unannotated: (
          await walkAll<TraceListItem>(
            (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, has_feedback: false }),
            "unannotated traces",
          )
        ).map((row) => row.request_id),
        settings: expectOk(await services.settings.get(sessions.owner), "settings"),
      });

      // A trace with *no* customer feedback: labelling one that already had some cannot detect a
      // label leaking into `has_feedback` or `feedback_count`.
      const firstUnannotated = (await customerViews(ids.availableRequestId)).unannotated[0];
      assert.ok(firstUnannotated !== undefined, "the harness needs a trace with no customer feedback");
      const before = await customerViews(firstUnannotated);
      assert.equal(before.feedback.length, 0, "and it really has none");
      assert.equal(before.detail.feedback_count, 0);

      const label = expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: firstUnannotated,
          rubric_version: 2,
          label: "incorrect",
          comment: "operator note the customer must not read",
          idempotency_key: "visibility-1",
        }),
        "calibration label on an unannotated trace",
      );
      const after = await customerViews(firstUnannotated);
      assert.deepEqual(after, before, "an operator label must change no customer view at all");
      assert.ok(
        !after.annotated.includes(firstUnannotated),
        "a labelled trace must not become annotated: a label is not customer feedback",
      );
      assert.ok(after.unannotated.includes(firstUnannotated), "and must stay in the unannotated set");

      // Not just the label this case created: *every* entry of the calibration set must be absent
      // from its own organization's customer views, seeded ones included.
      const labels = expectOk(
        await services.calibration.list(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "calibration set",
      );
      assert.ok(labels.items.some((entry) => entry.id === label.id), "the new label is in the set");
      assert.ok(labels.items.length > 1, "the harness needs a seeded label too, or only the new one is checked");
      for (const entry of labels.items) {
        assert.equal(entry.name, "calibration_label", "the calibration set holds labels only");
        assert.equal(entry.calibration_set, true);
        assert.equal(entry.author_role, "operator");
        assert.ok(entry.rubric_version !== null, "and each names its rubric");

        // Which organization owns the request? Ask each session; the one that can read it owns it.
        for (const session of [sessions.owner, sessions.member, sessions.otherOwner, sessions.suspendedOwner]) {
          const listed = await services.feedback.list(session, entry.request_id);
          if (!listed.ok) continue;
          assert.ok(
            !listed.value.some((item) => item.id === entry.id),
            `label ${entry.id} is visible in ${session.email}'s feedback list`,
          );
          const detail = await services.traceDetail(session, entry.request_id);
          if (detail.ok) {
            assert.ok(
              !detail.value.feedback.some((item) => item.id === entry.id),
              `label ${entry.id} is visible in ${session.email}'s trace detail`,
            );
            assert.equal(
              detail.value.feedback_count,
              detail.value.feedback.length,
              "feedback_count must count exactly the entries the customer can see",
            );
          }
          const serialized = JSON.stringify([listed.value, detail.ok ? detail.value : null]);
          for (const principal of operatorPrincipals) {
            assert.ok(!serialized.includes(principal), `label ${entry.id} exposed the operator ${principal}`);
          }
        }
      }

      // A label leaves no trace in the sequences a customer can observe either: the id and timestamp
      // their next submission gets must not shift because the platform looked at their request.
      const beforeSubmit = expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "comment",
          value: "before a label",
          idempotency_key: "sequence-1",
        }),
        "a submission before a label",
      );
      expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 1,
          label: "correct",
          idempotency_key: "sequence-label",
        }),
        "a label between two submissions",
      );
      const afterSubmit = expectOk(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "comment",
          value: "after a label",
          idempotency_key: "sequence-2",
        }),
        "a submission after a label",
      );
      assert.notEqual(afterSubmit.id, beforeSubmit.id, "two submissions are two entries");
      assert.ok(
        afterSubmit.created_at >= beforeSubmit.created_at,
        "the customer's own sequence still moves forward",
      );

      // A label is not a score, not a consent change, and does not touch the customer's own text.
      const annotated = expectOk(
        await services.feedback.list(sessions.owner, ids.availableRequestId),
        "an annotated trace's feedback",
      );
      const beforeDetail = expectOk(await services.traceDetail(sessions.owner, ids.availableRequestId), "detail");
      expectOk(
        await services.calibration.label(sessions.operator, {
          request_id: ids.availableRequestId,
          rubric_version: 3,
          label: "partially_correct",
          idempotency_key: "visibility-2",
        }),
        "a label on an annotated trace",
      );
      const afterDetail = expectOk(await services.traceDetail(sessions.owner, ids.availableRequestId), "detail after");
      assert.equal(afterDetail.score_count, beforeDetail.score_count, "a label is not a judge score");
      assert.equal(afterDetail.feedback_count, beforeDetail.feedback_count, "nor customer feedback");
      assert.deepEqual(
        expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "feedback after"),
        annotated,
        "and it leaves the customer's own comments exactly as they were",
      );
      assert.deepEqual(
        expectOk(await services.settings.get(sessions.owner), "settings after").consent_history,
        before.settings.consent_history,
        "a label is not a consent change",
      );

      // A client cannot author one through the ordinary submit path, whatever it sends.
      for (const [session, who] of [
        [sessions.owner, "customer"],
        [sessions.operator, "operator session"],
      ] as [SessionContext, string][]) {
        expectError(
          await services.feedback.submit(session, {
            request_id: ids.availableRequestId,
            name: "calibration_label" as never,
            value: "correct",
            idempotency_key: `visibility-forge-${who}`,
          }),
          "invalid_request",
          `feedback.submit must refuse the operator-only name for a ${who}`,
        );
      }
    });

    it("no operation accepts a field the caller invented", async () => {
      const { services, sessions, ids } = await makeHarness();
      const probes = operationProbes(services, sessions, ids);
      assertProbesCoverEveryOperation(probes);

      // Driven from the operation list: an operation added without an extra-field check fails the
      // coverage assertion above rather than passing silently.
      for (const [operation, probe] of Object.entries(probes)) {
        if (probe.withExtraField === undefined) continue;
        const session = (OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation)
          ? sessions.operator
          : sessions.owner;
        expectError(
          await settle(`${operation} with an invented field`, () => probe.withExtraField!(session)),
          "invalid_request",
          `${operation} must refuse a field it does not declare`,
        );
      }

      // Nor anything that is not an object at all, where one is expected.
      for (const [operation, probe] of Object.entries(probes)) {
        if (probe.withExtraField === undefined) continue;
        const session = (OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation)
          ? sessions.operator
          : sessions.owner;
        for (const [what, value] of [
          ["a string", "nonsense"],
          ["an array", []],
          ["null", null],
        ] as [string, unknown][]) {
          expectError(
            await settle(`${operation} given ${what}`, () => probe.asGiven(session, value)),
            "invalid_request",
            `${operation} must refuse ${what} where it expects an object`,
          );
        }
      }

      // And the other organization's state is of course untouched by any of it.
      const foreign = expectOk(await services.settings.get(sessions.otherOwner), "other organization settings");
      assert.ok(inSet(TRACE_MODES, foreign.trace_mode));
    });

    it("reads and writes never cross the tenant boundary, in either direction", async () => {
      const { services, sessions, ids } = await makeHarness();
      // B8: computed expectations, not "it returned something". A service that aggregated across
      // organizations, or read another organization's settings, would satisfy a shape check.
      const rowsOf = async (session: SessionContext): Promise<UsageRow[]> =>
        walkAll<UsageRow>((cursor) => services.usage(session, { limit: MAX_PAGE_LIMIT, cursor }), "usage");
      const expectedSummary = (rows: UsageRow[]) => {
        let cost = ZERO_MONEY;
        let prompt = 0;
        let completion = 0;
        let failed = 0;
        for (const row of rows) {
          cost = addMoney(cost, row.cost);
          prompt += row.prompt_tokens ?? 0;
          completion += row.completion_tokens ?? 0;
          if (row.http_status >= 400) failed += 1;
        }
        return { requests: rows.length, failed_requests: failed, prompt_tokens: prompt, completion_tokens: completion, cost };
      };

      for (const [session, who] of [
        [sessions.owner, "first organization"],
        [sessions.otherOwner, "second organization"],
      ] as [SessionContext, string][]) {
        const rows = await rowsOf(session);
        const summary = expectOk(await services.usageSummary(session, {}), `${who} summary`);
        const expected = expectedSummary(rows);
        assert.equal(summary.requests, expected.requests, `${who}: the summary counts this organization's rows only`);
        assert.equal(summary.cost, expected.cost, `${who}: and totals this organization's cost only`);
        assert.equal(summary.prompt_tokens, expected.prompt_tokens, `${who}: prompt tokens`);
        assert.equal(summary.completion_tokens, expected.completion_tokens, `${who}: completion tokens`);
        assert.equal(summary.failed_requests, expected.failed_requests, `${who}: failed requests`);

        const daily = expectOk(await services.usageDaily(session, {}), `${who} daily`);
        assert.equal(
          daily.reduce((sum, day) => sum + day.requests, 0),
          rows.length,
          `${who}: the daily breakdown covers exactly this organization's rows`,
        );
        const days = new Set(rows.map((row) => row.created_at.slice(0, 10)));
        assert.deepEqual(new Set(daily.map((day) => day.day)), days, `${who}: and exactly its days`);
      }

      // Two organizations with different data must not report the same figures.
      const ourSummary = expectOk(await services.usageSummary(sessions.owner, {}), "our summary");
      const theirSummary = expectOk(await services.usageSummary(sessions.otherOwner, {}), "their summary");
      assert.notEqual(ourSummary.requests, theirSummary.requests, "the harness needs differently sized tenants");

      // Settings are per organization, and a write on one is invisible to the other.
      const ourSettings = expectOk(await services.settings.get(sessions.owner), "our settings");
      const theirBefore = expectOk(await services.settings.get(sessions.otherOwner), "their settings");
      assert.notDeepEqual(ourSettings, theirBefore, "the harness needs two different settings records");
      const changed = expectOk(
        await services.settings.update(sessions.owner, {
          content_retention_days: ourSettings.content_retention_days === 11 ? 12 : 11,
          evaluation_consent: !ourSettings.evaluation_consent,
        }),
        "our settings update",
      );
      assert.notDeepEqual(changed, ourSettings, "the update must change something");
      assert.deepEqual(
        expectOk(await services.settings.get(sessions.otherOwner), "their settings after"),
        theirBefore,
        "another organization's settings must be byte-identical after our write",
      );
      // And the reverse direction: their write must land on *their* record, not on the first
      // organization's. A service that always wrote the first one passes if only we ever write.
      const ourAfterTheirWrite = expectOk(await services.settings.get(sessions.owner), "our settings before theirs");
      const theirChanged = expectOk(
        await services.settings.update(sessions.otherOwner, {
          content_retention_days: theirBefore.content_retention_days === 9 ? 10 : 9,
        }),
        "their settings update",
      );
      assert.notEqual(
        theirChanged.content_retention_days,
        theirBefore.content_retention_days,
        "their write must change their record",
      );
      assert.deepEqual(
        expectOk(await services.settings.get(sessions.owner), "our settings after theirs"),
        ourAfterTheirWrite,
        "our settings must be byte-identical after their write",
      );
      expectOk(await services.keys.create(sessions.owner, { name: "isolation probe" }), "our key");
      assert.deepEqual(
        expectOk(await services.keys.list(sessions.otherOwner), "their keys after"),
        expectOk(await services.keys.list(sessions.otherOwner), "their keys again"),
        "their key list is stable",
      );
      assert.deepEqual(
        expectOk(await services.settings.get(sessions.otherOwner), "their settings after our key"),
        theirChanged,
        "and their settings still exactly as they last set them",
      );
    });

    it("a filter filters, and an absent filter does not", async () => {
      const { services, sessions, ids } = await makeHarness();
      const all = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "usage",
      );
      assert.ok(all.length > 2, "the harness needs a few rows");

      const models = new Set(all.map((row) => row.model));
      assert.ok(models.size > 1, "the harness needs more than one model, or the model filter is untestable");
      for (const model of models) {
        const filtered = await walkAll<UsageRow>(
          (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, model }),
          `usage by model ${model}`,
        );
        assert.deepEqual(
          filtered.map((row) => row.request_id).sort(),
          all.filter((row) => row.model === model).map((row) => row.request_id).sort(),
          `the model filter must return exactly the rows of ${model}`,
        );
        assert.ok(filtered.length < all.length, "and fewer than all of them");
      }

      const keyIds = new Set(all.map((row) => row.key_id));
      assert.ok(keyIds.size > 1, "the harness needs rows from more than one key");
      const [someKey] = [...keyIds];
      const byKey = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, key_id: someKey }),
        "usage by key",
      );
      assert.deepEqual(
        byKey.map((row) => row.request_id).sort(),
        all.filter((row) => row.key_id === someKey).map((row) => row.request_id).sort(),
        "the key filter must return exactly that key's rows",
      );

      // A time window: everything strictly newer than the median row's timestamp.
      const sorted = [...all].sort((a, b) => a.created_at.localeCompare(b.created_at));
      const pivot = sorted[Math.floor(sorted.length / 2)].created_at;
      const since = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, from: pivot }),
        "usage from the pivot",
      );
      assert.deepEqual(
        since.map((row) => row.request_id).sort(),
        all.filter((row) => row.created_at >= pivot).map((row) => row.request_id).sort(),
        "from must exclude everything older",
      );
      assert.ok(since.length < all.length, "and it must exclude something");
      const until = await walkAll<UsageRow>(
        (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, to: pivot }),
        "usage to the pivot",
      );
      assert.ok(until.length < all.length, "to must exclude something too");
      assert.equal(
        since.length + until.length - all.filter((row) => row.created_at === pivot).length,
        all.length,
        "the two halves must partition the rows, counting the boundary once",
      );

      // The trace list has its own copy of every filter, so each must be exercised there too.
      const allTraces = await walkAll<TraceListItem>(
        (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "traces",
      );
      for (const model of new Set(allTraces.map((row) => row.model))) {
        const filtered = await walkAll<TraceListItem>(
          (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, model }),
          `traces by model ${model}`,
        );
        assert.deepEqual(
          filtered.map((row) => row.request_id).sort(),
          allTraces.filter((row) => row.model === model).map((row) => row.request_id).sort(),
          `the trace model filter must return exactly the rows of ${model}`,
        );
        assert.ok(filtered.length < allTraces.length, "and fewer than all of them");
      }
      for (const state of new Set(allTraces.map((row) => row.job_state))) {
        const filtered = await walkAll<TraceListItem>(
          (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, job_state: state }),
          `traces by job state ${state}`,
        );
        assert.deepEqual(
          filtered.map((row) => row.request_id).sort(),
          allTraces.filter((row) => row.job_state === state).map((row) => row.request_id).sort(),
          `the job_state filter must return exactly the ${state} rows`,
        );
      }

      // And a trace filter on a closed vocabulary.
      const traces = await walkAll<TraceListItem>(
        (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
        "traces",
      );
      const states = new Set(traces.map((row) => row.content));
      assert.ok(states.size > 1, "the harness needs more than one content state");
      for (const state of states) {
        const filtered = await walkAll<TraceListItem>(
          (cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, content: state }),
          `traces with content ${state}`,
        );
        assert.deepEqual(
          filtered.map((row) => row.request_id).sort(),
          traces.filter((row) => row.content === state).map((row) => row.request_id).sort(),
          `the content filter must return exactly the ${state} rows`,
        );
      }
      assert.ok(ids.modelId.length > 0);
    });

    it("the operator list pages like every other list", async () => {
      const { services, sessions } = await makeHarness();
      const all = await assertSamePagesAtEveryLimit<AdminOrgSummary>(
        (limit, cursor) => services.adminOrgs(sessions.operator, { limit, cursor }),
        (row) => row.org_id,
        "adminOrgs",
      );
      // Walked one row at a time as well: the operator list is the one ascending list, and an
      // off-by-one in its resume shows up only at a small page size.
      const single = await walkAll<AdminOrgSummary>(
        (cursor) => services.adminOrgs(sessions.operator, { limit: 1, cursor }),
        "adminOrgs at limit 1",
      );
      assert.deepEqual(
        single.map((row) => row.org_id),
        all.map((row) => row.org_id),
        "walking the operator list one row at a time must return the same organizations in the same order",
      );
      assert.ok(all.length >= 3, "the harness has at least three organizations");
      for (const row of all) {
        assert.ok(isMoney(row.balance.ledger_total), "every listed organization has a wallet");
      }
      assert.ok(
        all.some((row) => compareMoney(row.balance.ledger_total, ZERO_MONEY) !== 0),
        "and at least one of them is not zero, so a constant-zero balance fails here",
      );
    });
  });
}
