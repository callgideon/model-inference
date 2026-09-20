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
import { addMoney, compareMoney, isMoney, parseMoney, subMoney, ZERO_MONEY, type Money } from "./money.ts";
import {
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
  MAX_FEEDBACK_TEXT_CHARS,
  MAX_IDEMPOTENCY_KEY_CHARS,
  MAX_PAGE_LIMIT,
  SETTLEMENT_STATES,
  TERMINAL_CAUSES,
  TERMINAL_JOB_STATES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_LOSS_REASONS,
  TRACE_MODES,
  USAGE_CERTAINTIES,
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
import type { ConsoleServices } from "./services.ts";

export type ConsoleHarness = {
  services: ConsoleServices;
  sessions: {
    owner: SessionContext;
    member: SessionContext;
    operator: SessionContext;
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
  throw new Error(`${what}: pagination did not terminate`);
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
function assertHasTimestampTie(rows: { at: string; id: string }[], what: string): void {
  const tied = rows.some((row, index) => index > 0 && rows[index - 1].at === row.at);
  assert.ok(
    tied,
    `${what}: the harness needs at least two rows sharing a created_at, or the keyset tie-break is untested`,
  );
}

/** R19: an operator label is the only entry that may claim operator authorship or membership. */
function assertFeedbackEntry(entry: FeedbackEntry, what: string): void {
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
      assertHasTimestampTie(ledgerKeys, "ledger");
      assertStrictTotalOrder(ledgerKeys, "ledger");
      for (const entry of ledger) assertLedgerEntry(entry);

      const traces = await assertSamePagesAtEveryLimit<TraceListItem>(
        (limit, cursor) => services.traces(sessions.owner, { limit, cursor }),
        (row) => row.request_id,
        "traces",
      );
      const traceKeys = traces.map((row) => ({ at: row.created_at, id: row.request_id }));
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
      // Correctly *shaped* cursors: the point is that the scope hash is checked, not that a
      // malformed blob is rejected. An offset-shaped forgery would pass a keyset implementation
      // trivially and prove nothing.
      const decoded = JSON.parse(atob(cursor)) as Record<string, unknown>;
      expectError(
        await services.usage(sessions.owner, {
          limit: 5,
          cursor: btoa(JSON.stringify({ ...decoded, k: "deadbeef" })),
        }),
        "invalid_cursor",
        "cursor with a forged scope",
      );
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
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: `${cursor}x` }),
        "invalid_cursor",
        "mutated cursor",
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
      const { services, sessions } = await makeHarness();
      const balance = expectOk(await services.balances(sessions.owner), "balances");
      for (const value of [balance.ledger_total, balance.reserved_total, balance.available]) {
        assert.ok(isMoney(value), `balance value is not canonical money: ${value}`);
      }
      assert.equal(
        balance.available,
        subMoney(balance.ledger_total, balance.reserved_total),
        "available must be total minus reserved",
      );
      const ledger = await walkAll<LedgerEntry>(
        (cursor) => services.ledger(sessions.owner, { limit: 50, cursor }),
        "ledger",
      );
      let total = ZERO_MONEY;
      for (const entry of ledger) total = addMoney(total, entry.delta);
      assert.equal(balance.ledger_total, total, "the ledger total must equal the sum of its entries");
      if (compareMoney(balance.reserved_total, ZERO_MONEY) === 1) {
        assert.equal(compareMoney(balance.available, balance.ledger_total), -1, "a hold must reduce available");
      }

      const usage = expectOk(await services.usageSummary(sessions.owner, {}), "usage summary");
      assert.ok(isMoney(usage.cost), "summary cost");
      assert.ok(isMoney(usage.pending_reconciliation), "summary pending_reconciliation");
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
      assert.match(latest.changed_at, RFC3339);
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
      const served = ERROR_CODES.filter((code) => ERROR_CODE_HTTP_STATUS[code] !== null);
      assert.equal(served.length, 27, "27 codes carry an HTTP status");
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

    it("a suspended organization can do nothing, and suspension leaves its accounting alone", async () => {
      const { services, sessions, ids } = await makeHarness();
      // R18: reachable from the harness, not something the suite has to arrange first.
      expectError(await services.usage(sessions.suspendedOwner, {}), "org_suspended", "suspended usage");
      expectError(await services.balances(sessions.suspendedOwner), "org_suspended", "suspended balances");
      expectError(await services.settings.get(sessions.suspendedOwner), "org_suspended", "suspended settings");
      expectError(
        await services.keys.create(sessions.suspendedOwner, { name: "while suspended" }),
        "org_suspended",
        "suspended key creation",
      );
      expectError(
        await services.feedback.submit(sessions.suspendedOwner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: true,
          idempotency_key: "suspended-feedback",
        }),
        "org_suspended",
        "suspended feedback",
      );

      // The operator still sees it, with its reason, and its wallet still adds up.
      const listed = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      );
      const row = listed.items.find((candidate) => candidate.org_id === ids.suspendedOrgId);
      assert.ok(row !== undefined, "a suspended organization is still listed for the operator");
      assert.equal(row.suspended, true);
      assert.ok(row.suspension_reason !== null && row.suspension_reason.length > 0, "suspension says why");
      assert.ok(isMoney(row.balance.ledger_total), "a suspended organization still has a wallet");
    });

    it("an operator can suspend and restore an organization without touching what it already owes", async () => {
      const { services, sessions, ids } = await makeHarness();
      const accounting = async (): Promise<string> => {
        const ledger = await walkAll<LedgerEntry>(
          (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
          "ledger",
        );
        const usage = await walkAll<UsageRow>(
          (cursor) => services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
          "usage",
        );
        return JSON.stringify([
          ledger.map((entry) => [entry.id, entry.delta, entry.kind]),
          usage.map((row) => [row.request_id, row.cost, row.settlement_state, row.max_hold]),
        ]);
      };
      const before = await accounting();

      const denied = await services.adminSetSuspension(sessions.owner, {
        target_org_id: ids.orgId,
        suspended: true,
        reason: "owner trying to suspend itself",
        idempotency_key: "suspend-denied",
      });
      expectError(denied, "forbidden", "an owner cannot suspend an organization");

      const suspended = expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: true,
          reason: "conformance: suspend",
          idempotency_key: "suspend-1",
        }),
        "operator suspension",
      );
      assert.equal(suspended.suspended, true);
      assert.equal(suspended.suspension_reason, "conformance: suspend");
      expectError(await services.usage(sessions.owner, {}), "org_suspended", "usage while suspended");

      // Replay is one effect, and a changed payload under the same key is a conflict.
      const replay = expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: true,
          reason: "conformance: suspend",
          idempotency_key: "suspend-1",
        }),
        "replayed suspension",
      );
      assert.equal(replay.suspended, true);
      expectError(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: false,
          reason: "conformance: suspend",
          idempotency_key: "suspend-1",
        }),
        "idempotency_conflict",
        "the same key flipped to a different state",
      );
      expectError(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: true,
          reason: "   ",
          idempotency_key: "suspend-no-reason",
        }),
        "invalid_request",
        "suspension without a reason",
      );

      const restored = expectOk(
        await services.adminSetSuspension(sessions.operator, {
          target_org_id: ids.orgId,
          suspended: false,
          reason: "conformance: restore",
          idempotency_key: "suspend-2",
        }),
        "operator restore",
      );
      assert.equal(restored.suspended, false);
      assert.equal(await accounting(), before, "suspension must not alter existing accounting rows");
    });

    it("the three entitlement states are distinct, and the empty one is a denial", async () => {
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

      // R24: the harness carries one organization of each kind, because "not configured" and
      // "entitled to nothing" are different facts and a page must not render them the same way.
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
        }
        if (list === null) {
          assert.equal(row.entitlements.updated_at, null, "an unrecorded default has no audit stamp");
        }
      }
      assert.deepEqual([...kinds].sort(), ["default", "explicit", "none"], "all three states must be present");

      // Setting each state, and the payload distinction between them.
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
    });

    it("an operator sets entitlements, and only the names the contract knows", async () => {
      const { services, sessions, ids } = await makeHarness();
      const set = expectOk(
        await services.adminSetEntitlements(sessions.operator, {
          target_org_id: ids.orgId,
          model_ids: [ids.modelId],
          limits: { max_concurrent_requests: 4 },
          reason: "conformance: entitle",
          idempotency_key: "entitle-1",
        }),
        "operator entitlements",
      );
      assert.deepEqual(set.model_ids, [ids.modelId]);
      assert.equal(set.limits.max_concurrent_requests, 4);
      assert.ok(set.updated_at !== null && set.updated_by === sessions.operator.email, "the write is audited");

      const listed = expectOk(
        await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }),
        "operator org list",
      );
      const row = listed.items.find((candidate) => candidate.org_id === ids.orgId);
      assert.ok(row !== undefined);
      assert.deepEqual(row.entitlements.model_ids, [ids.modelId], "the list shows what was set");

      expectError(
        await services.adminSetEntitlements(sessions.owner, {
          target_org_id: ids.orgId,
          model_ids: null,
          limits: {},
          reason: "owner attempt",
          idempotency_key: "entitle-denied",
        }),
        "forbidden",
        "an owner cannot set entitlements",
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
          limits: { max_concurrent_requests: -1 },
          reason: "conformance",
          idempotency_key: "entitle-negative",
        }),
        "invalid_request",
        "a negative entitlement limit",
      );
      for (const name of ENTITLEMENT_LIMIT_NAMES) {
        assert.equal(typeof name, "string", "the limit vocabulary is a closed list");
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

    it("every mutating operation refuses an injected failure without half-changing anything", async () => {
      const { services, sessions, ids } = await makeHarness();
      const snapshot = async () => ({
        ledger: (
          await walkAll<LedgerEntry>(
            (cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }),
            "ledger",
          )
        ).map((entry) => entry.id),
        keys: expectOk(await services.keys.list(sessions.owner), "keys").map((key) => `${key.id}:${key.revoked_at}`),
        settings: expectOk(await services.settings.get(sessions.owner), "settings"),
        feedback: expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "feedback").length,
      });

      // Each refusal below is reached through a different validation path, one per mutating
      // operation, and each must be a no-op.
      const before = await snapshot();
      expectError(
        await settle("grant with a bad amount", () =>
          services.adminGrant(sessions.operator, {
            target_org_id: ids.orgId,
            amount: "1e2" as Money,
            kind: "promotional",
            reason: "refusal drill",
            idempotency_key: "refusal-grant",
          }),
        ),
        "invalid_request",
        "grant with an exponent amount",
      );
      expectError(
        await settle("feedback with a bad value", () =>
          services.feedback.submit(sessions.owner, {
            request_id: ids.availableRequestId,
            name: "rating",
            value: 9,
            idempotency_key: "refusal-feedback",
          }),
        ),
        "invalid_request",
        "feedback with a rating out of range",
      );
      expectError(
        await settle("settings with a bad retention", () =>
          services.settings.update(sessions.owner, { content_retention_days: MAX_CONTENT_RETENTION_DAYS + 1 }),
        ),
        "invalid_request",
        "settings above the retention cap",
      );
      expectError(
        await settle("key without a name", () => services.keys.create(sessions.owner, { name: "  " })),
        "invalid_request",
        "key with a blank name",
      );
      expectError(
        await settle("revoke of an unknown key", () => services.keys.revoke(sessions.owner, ids.unknownRequestId)),
        "not_found",
        "revocation of a key that does not exist",
      );
      assert.deepEqual(await snapshot(), before, "a refused mutation changed the state anyway");
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

      // Same key, different target or different value: a conflict, never a second effect.
      expectError(
        await services.adminGrant(sessions.operator, { ...grant, target_org_id: ids.otherOrgId }),
        "idempotency_conflict",
        "the same key against another organization",
      );
      expectError(
        await services.adminGrant(sessions.operator, { ...grant, amount: "2.00000000" as Money }),
        "idempotency_conflict",
        "the same key with another amount",
      );

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
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          name: "thumb",
          value: false,
          idempotency_key: key,
        }),
        "idempotency_conflict",
        "the same feedback key with another value",
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
      const minted: FeedbackEntry[] = [];
      for (let i = 0; i < 12; i += 1) {
        minted.push(
          expectOk(
            await services.feedback.submit(sessions.owner, {
              request_id: ids.availableRequestId,
              name: "comment",
              value: `note ${i}`,
              idempotency_key: `id-probe-feedback-${i}`,
            }),
            `feedback ${i}`,
          ),
        );
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

    it("a filtered walk survives the cursor's own row leaving the filter", async () => {
      const { services, sessions } = await makeHarness();
      const query = { limit: 5, has_feedback: false } as const;
      const first = expectOk(await services.traces(sessions.owner, query), "first filtered page");
      assert.ok(first.next_cursor !== null, "the harness needs more than one page of unannotated traces");
      assert.ok(first.items.length > 0);
      const cursorRow = first.items[first.items.length - 1];

      // Annotate exactly the row the cursor points at: it now has feedback, so it no longer matches
      // `has_feedback: false`. A cursor that resumes by *looking up* its row would 400 here; a
      // keyset cursor resumes after the key and carries on (N2).
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
      const seen = [...first.items, ...second.items].map((row) => row.request_id);
      assert.equal(new Set(seen).size, seen.length, "a row was repeated after the cursor row left the filter");
      for (const row of second.items) {
        assert.equal(row.feedback_count, 0, "the filter still holds for the rows that follow");
        assert.ok(
          row.created_at <= cursorRow.created_at,
          "the walk resumed after the cursor key, not at the start",
        );
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

    it("no operation accepts a field the caller invented", async () => {
      const { services, sessions, ids } = await makeHarness();
      // The point of the rule: a caller that thinks it can pick the tenant, the author role or the
      // storage location learns that it cannot, instead of getting a page that looks right.
      expectError(
        await services.usage(sessions.owner, { org_id: ids.otherOrgId } as never),
        "invalid_request",
        "usage with a caller-supplied organization",
      );
      expectError(
        await services.traces(sessions.owner, { storage_key: "s3://bucket/key" } as never),
        "invalid_request",
        "traces with a caller-supplied storage key",
      );
      expectError(
        await services.ledger(sessions.owner, { org_id: ids.otherOrgId } as never),
        "invalid_request",
        "ledger with a caller-supplied organization",
      );
      expectError(
        await services.settings.update(sessions.owner, { org_id: ids.otherOrgId } as never),
        "invalid_request",
        "settings update aimed at another organization",
      );
      expectError(
        await services.keys.create(sessions.owner, { name: "smuggled", org_id: ids.otherOrgId } as never),
        "invalid_request",
        "key creation aimed at another organization",
      );
      expectError(
        await services.judgeRuns(sessions.owner, { org_id: ids.otherOrgId } as never),
        "invalid_request",
        "judge runs aimed at another organization",
      );
      // The other organization's state is of course still untouched.
      const foreign = expectOk(await services.settings.get(sessions.otherOwner), "other organization settings");
      assert.ok(inSet(TRACE_MODES, foreign.trace_mode));
    });
  });
}
