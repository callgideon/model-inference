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
  ERROR_CODES,
  EXECUTION_MODES,
  FEEDBACK_CHANNELS,
  FEEDBACK_RATINGS,
  JOB_STATES,
  JUDGE_MODES,
  JUDGE_RUN_STATES,
  JUDGE_SCORE_KINDS,
  LEDGER_ENTRY_KINDS,
  ERROR_CODE_HTTP_STATUS,
  MAX_CONTENT_RETENTION_DAYS,
  MAX_FEEDBACK_TEXT_CHARS,
  MAX_PAGE_LIMIT,
  SETTLEMENT_STATES,
  TERMINAL_CAUSES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_LOSS_REASONS,
  TRACE_MODES,
  USAGE_CERTAINTIES,
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
  };
};

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

function assertUsageRow(row: UsageRow): void {
  assert.match(row.request_id, UUID, "usage request_id");
  assert.match(row.created_at, RFC3339, "usage created_at");
  assert.ok(inSet(JOB_STATES, row.job_state), "usage job_state");
  assert.ok(row.terminal_cause === null || inSet(TERMINAL_CAUSES, row.terminal_cause), "usage terminal_cause");
  assert.ok(inSet(EXECUTION_MODES, row.execution_mode), "usage execution_mode");
  assert.ok(inSet(USAGE_CERTAINTIES, row.usage_certainty), "usage usage_certainty");
  assert.ok(inSet(SETTLEMENT_STATES, row.settlement_state), "usage settlement_state");
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
}

function assertTraceListItem(row: TraceListItem): void {
  assert.match(row.request_id, UUID, "trace request_id");
  assert.ok(inSet(TRACE_MODES, row.trace_mode), "trace trace_mode");
  assert.ok(inSet(TRACE_CONTENT_AVAILABILITY, row.content), "trace content availability");
  assert.ok(inSet(TRACE_LOSS_REASONS, row.loss_reason), "trace loss_reason");
  assert.ok(isMoney(row.cost), "trace cost");
  assert.ok(Number.isInteger(row.feedback_count) && row.feedback_count >= 0, "trace feedback_count");
  if (row.trace_mode === "off") {
    assert.equal(row.content, "off", "an off-mode request has no trace content");
  }
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
      for (const entry of ledger) assertLedgerEntry(entry);

      const traces = await assertSamePagesAtEveryLimit<TraceListItem>(
        (limit, cursor) => services.traces(sessions.owner, { limit, cursor }),
        (row) => row.request_id,
        "traces",
      );
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
      const { services, sessions } = await makeHarness();
      const first = expectOk(await services.usage(sessions.owner, { limit: 5 }), "usage first page");
      assert.ok(first.next_cursor !== null, "the fixture needs more than one page");
      const cursor = first.next_cursor;

      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: "not-a-cursor" }),
        "invalid_cursor",
        "garbage cursor",
      );
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: btoa('{"o":0,"k":"deadbeef"}') }),
        "invalid_cursor",
        "forged cursor",
      );
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor: `${cursor}x` }),
        "invalid_cursor",
        "mutated cursor",
      );
      // A cursor is bound to the query that produced it, so filters cannot change mid-walk.
      expectError(
        await services.usage(sessions.owner, { limit: 5, cursor, model: "marlin-2b@2026-09-01" }),
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
        await services.feedback.submit(sessions.owner, { request_id: ids.otherOrgRequestId, rating: "up" }),
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

    it("feedback appears immediately, with provenance the client cannot set", async () => {
      const { services, sessions, ids } = await makeHarness();
      const before = expectOk(
        await services.feedback.list(sessions.owner, ids.availableRequestId),
        "feedback before",
      );
      // A client that sends provenance fields gets them ignored, not honoured.
      const spoofed = {
        request_id: ids.availableRequestId,
        rating: "down",
        comment: "cut off early",
        correction: "two forklifts",
        channel: "api",
        author_role: "judge",
        author_principal: "someone-else@example.com",
        calibration_set: true,
      } as unknown as Parameters<ConsoleServices["feedback"]["submit"]>[1];
      const entry = expectOk(await services.feedback.submit(sessions.owner, spoofed), "feedback submit");

      assert.equal(entry.channel, "console", "the console sets the channel");
      assert.equal(entry.author_role, "customer", "a customer session stays a customer label");
      assert.equal(entry.author_principal, sessions.owner.email, "the principal comes from the session");
      assert.equal(entry.calibration_set, false, "a client cannot enrol its own feedback for calibration");
      assert.ok(inSet(FEEDBACK_CHANNELS, entry.channel) && inSet(AUTHOR_ROLES, entry.author_role));
      assert.ok(inSet(FEEDBACK_RATINGS, entry.rating));
      assert.match(entry.created_at, RFC3339, "feedback created_at");

      // Accepted durably means visible now, whatever the projection is doing.
      const after = expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId), "feedback after");
      assert.equal(after.length, before.length + 1, "accepted feedback must be listed immediately");
      assert.ok(after.some((item) => item.id === entry.id), "the accepted entry is missing from the list");

      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          rating: "sideways" as unknown as "up",
        }),
        "invalid_request",
        "invalid rating",
      );
      // Free text is a note, not an upload channel, and not a place to smuggle a structure.
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          rating: "up",
          comment: "x".repeat(MAX_FEEDBACK_TEXT_CHARS + 1),
        }),
        "invalid_request",
        "oversized comment",
      );
      expectError(
        await services.feedback.submit(sessions.owner, {
          request_id: ids.availableRequestId,
          rating: "up",
          correction: { nested: true } as unknown as string,
        }),
        "invalid_request",
        "non-string correction",
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
          target_org_id: "33333333-3333-4333-8333-333333333333",
          idempotency_key: "grant-conformance-unknown-org",
        }),
        "not_found",
        "grant to an unknown organization",
      );
    });

    it("content availability decides the payload and never leaks a storage reference", async () => {
      const { services, sessions } = await makeHarness();
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
}
