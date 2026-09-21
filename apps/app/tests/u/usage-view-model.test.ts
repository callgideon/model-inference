// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// U1 usage view model. Every case names the invariant it holds, and every invariant here is
// declared as a mutant in tests/u/run-mutants.mjs (R32): a case that cannot be broken is a case
// that is not checking anything.
//
// The service is the fixture-backed fake (`createFakeConsoleServices`), because the invariants worth
// testing — a cursor bound to its filters, a hold that is not a charge, money that stays exact — are
// about how the page uses the contract, not about invented data.
import assert from "node:assert/strict";
import test from "node:test";

import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { displayMoney, parseMoney, ZERO_MONEY } from "../../lib/contracts/money.ts";
import {
  MAX_PAGE_LIMIT,
  USAGE_QUERY_FIELDS,
  type SettlementState,
  type UsageRow,
} from "../../lib/contracts/types.ts";
import {
  ALL,
  DEFAULT_RANGE,
  PAGE_SIZE,
  RANGES,
  amountView,
  dayViews,
  explainError,
  firstCursorState,
  hasPreviousPage,
  instantLabel,
  ledgerHref,
  modelOptions,
  nextCursorState,
  pageNumberOf,
  parsePageCursor,
  parseUsageFilters,
  previousCursorState,
  recoveryFor,
  settlementView,
  summaryTiles,
  tokensView,
  usageHref,
  usagePageQuery,
  usageRowView,
  usageScopeQuery,
  usageSearch,
  viewStateOf,
  withFilter,
  type UsageFilters,
} from "../../app/(console)/usage/view-model.ts";

const NOW = new Date("2026-09-20T12:00:00.000Z");

/** The widest range the picker offers; the fixture's rows stretch further back than 30 days. */
const WIDE: UsageFilters = { range: "30d", keyId: null, model: null, cursor: null, trail: [] };

function services() {
  return createFakeConsoleServices();
}

/** The whole scope, walked at the contract's maximum page size: one page is not the scope. */
async function rowsOf(filters: UsageFilters, limit = MAX_PAGE_LIMIT): Promise<UsageRow[]> {
  const fake = services();
  const rows: UsageRow[] = [];
  let cursor: string | null = null;
  for (let guard = 0; guard < 50; guard += 1) {
    const query = { ...usageScopeQuery(filters, NOW), limit, ...(cursor === null ? {} : { cursor }) };
    const page = await fake.usage(fake.sessions.owner, query);
    assert.ok(page.ok, "the fixture scope must be readable");
    rows.push(...page.value.items);
    cursor = page.value.next_cursor;
    if (cursor === null) return rows;
  }
  throw new Error("the fixture walk did not terminate");
}

// ---------------------------------------------------------------------------
// URL state
// ---------------------------------------------------------------------------

test("U1-T01 the URL is untrusted: an unrecognised range falls back, `all` means no filter", () => {
  assert.equal(parseUsageFilters({}).range, DEFAULT_RANGE);
  assert.equal(parseUsageFilters({ range: "1000y" }).range, DEFAULT_RANGE);
  assert.equal(parseUsageFilters({ range: "" }).range, DEFAULT_RANGE);
  assert.equal(parseUsageFilters({ range: "7d" }).range, "7d");

  assert.equal(parseUsageFilters({ key: ALL }).keyId, null, "`all` is the absence of a filter");
  assert.equal(parseUsageFilters({ model: ALL }).model, null);
  assert.equal(parseUsageFilters({ key: "k_1" }).keyId, "k_1");
  assert.equal(parseUsageFilters({ model: "marlin-2b@2026-09-01" }).model, "marlin-2b@2026-09-01");

  // A repeated parameter is a fact of URLs; the last value wins rather than `["a","b"]` reaching a query.
  assert.equal(parseUsageFilters({ range: ["7d", "1h"] }).range, "1h");
  assert.equal(parseUsageFilters({ key: ["a", "b"] }).keyId, "b");

  const walked = parseUsageFilters({ cursor: "c3", trail: ["c1", "c2"] });
  assert.equal(walked.cursor, "c3");
  assert.deepEqual(walked.trail, ["c1", "c2"]);
  assert.deepEqual(parsePageCursor({ trail: "c1" }).trail, ["c1"], "one page back is not an array yet");
  assert.deepEqual(parsePageCursor({ cursor: "", trail: ["", "c1"] }), { cursor: null, trail: ["c1"] });
});

test("U1-T02 serializing and parsing a filter state round-trips, and defaults stay out of the URL", () => {
  const plain: UsageFilters = { range: DEFAULT_RANGE, keyId: null, model: null, cursor: null, trail: [] };
  assert.equal(usageSearch(plain), "", "page 1 of an unfiltered view is a bare path");
  assert.equal(usageHref(plain), "/usage");

  const walked: UsageFilters = {
    range: "7d",
    keyId: "key-1",
    model: "marlin-2b@2026-09-01",
    cursor: "c3",
    trail: ["c1", "c2"],
  };
  const search = usageSearch(walked);
  assert.deepEqual(parseUsageFilters(Object.fromEntries(searchEntries(search))), walked);
  assert.equal(usageHref(walked), `/usage?${search}`);
  assert.equal(ledgerHref({ cursor: "c1", trail: [] }), "/billing?cursor=c1");
  assert.equal(ledgerHref({ cursor: null, trail: [] }), "/billing", "a bare first page has no query");
});

/** URLSearchParams collapses repeats; the console reads Next's array form, so rebuild it. */
function searchEntries(search: string): [string, string | string[]][] {
  const grouped = new Map<string, string[]>();
  for (const [name, value] of new URLSearchParams(search)) {
    grouped.set(name, [...(grouped.get(name) ?? []), value]);
  }
  return [...grouped].map(([name, values]) => [name, values.length === 1 ? values[0] : values]);
}

test("U1-T03 the query carries only contract fields, and the service accepts it", async () => {
  const filters: UsageFilters = { ...WIDE, range: "6h", keyId: "key-x", model: "model-y", cursor: "c1" };
  const scope = usageScopeQuery(filters, NOW);
  const page = usagePageQuery(filters, NOW);

  for (const query of [scope, page]) {
    for (const name of Object.keys(query)) {
      assert.ok(
        (USAGE_QUERY_FIELDS as readonly string[]).includes(name),
        `${name} is not a UsageQuery field — an unknown field is invalid_request`,
      );
    }
  }
  assert.equal(scope.cursor, undefined, "a summary is of the whole scope, never of one page");
  assert.equal(scope.limit, undefined);
  assert.equal(scope.key_id, "key-x", "the key filter reaches the service");
  assert.equal(scope.model, "model-y", "and so does the model filter");
  assert.equal(usageScopeQuery({ ...filters, keyId: null, model: null }, NOW).key_id, undefined);
  assert.equal(page.cursor, "c1");
  assert.equal(page.limit, PAGE_SIZE);
  assert.equal(page.to, "2026-09-20T12:00:00.000Z", "`to` is the injected clock, never wall time");
  assert.equal(
    page.from,
    new Date(NOW.getTime() - RANGES["6h"]).toISOString(),
    "`from` is `to` minus the selected range",
  );

  const fake = services();
  const accepted = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  assert.ok(accepted.ok, "the service must accept the query the page builds");
  const summarised = await fake.usageSummary(fake.sessions.owner, usageScopeQuery(WIDE, NOW));
  assert.ok(summarised.ok);
});

// ---------------------------------------------------------------------------
// Keyset pagination
// ---------------------------------------------------------------------------

test("U1-T04 a keyset walk forward and back visits exactly the same pages, in order", async () => {
  const fake = services();
  const session = fake.sessions.owner;

  const forward: { filters: UsageFilters; ids: string[] }[] = [];
  let filters = WIDE;
  for (;;) {
    const result = await fake.usage(session, usagePageQuery(filters, NOW));
    assert.ok(result.ok, "every page of the walk must be readable");
    forward.push({ filters, ids: result.value.items.map((row) => row.request_id) });
    assert.equal(pageNumberOf(filters), forward.length, "the page number follows the walk");
    if (result.value.next_cursor === null) break;
    assert.ok(forward.length < 50, "the fixture walk must terminate");
    filters = nextCursorState(filters, result.value.next_cursor);
  }

  assert.ok(forward.length >= 4, `expected several pages, walked ${forward.length}`);
  const walked = forward.flatMap((entry) => entry.ids);
  assert.equal(new Set(walked).size, walked.length, "a keyset walk must not repeat a row");

  const whole = await rowsOf(WIDE);
  assert.deepEqual(walked, whole.map((row) => row.request_id), "no row is dropped at a page boundary");

  // Walking back must land on the same pages. This is the only thing the cursor trail is for: an
  // opaque cursor cannot be decremented, so a lost trail entry silently skips a page.
  let back = forward[forward.length - 1].filters;
  for (let index = forward.length - 1; index >= 0; index -= 1) {
    assert.deepEqual(back, forward[index].filters, `page ${index + 1} must be reachable backwards`);
    const result = await fake.usage(session, usagePageQuery(back, NOW));
    assert.ok(result.ok);
    assert.deepEqual(result.value.items.map((row) => row.request_id), forward[index].ids);
    assert.equal(hasPreviousPage(back), index > 0);
    back = previousCursorState(back);
  }
  assert.deepEqual(back, forward[0].filters, "stepping back from page 1 stays on page 1");
  assert.deepEqual(firstCursorState(forward[forward.length - 1].filters), forward[0].filters);
});

test("U1-T05 changing a filter discards the cursor, because a cursor is bound to its filters", async () => {
  const fake = services();
  const first = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  assert.ok(first.ok && first.value.next_cursor !== null);
  const second = nextCursorState(WIDE, first.value.next_cursor);

  const changed = withFilter(second, { model: "marlin-2b@2026-09-01" });
  assert.equal(changed.cursor, null, "a filter change starts the walk again");
  assert.deepEqual(changed.trail, []);
  assert.equal(changed.model, "marlin-2b@2026-09-01");
  assert.equal(changed.range, second.range, "the other filters survive");
  const accepted = await fake.usage(fake.sessions.owner, usagePageQuery(changed, NOW));
  assert.ok(accepted.ok, "the reset state is a query the service accepts");

  // Why the reset is not cosmetic: the same cursor under changed filters is refused.
  const kept = await fake.usage(fake.sessions.owner, {
    ...usagePageQuery(second, NOW),
    model: "marlin-2b@2026-09-01",
  });
  assert.ok(!kept.ok, "a cursor reused under different filters must be refused");
  assert.equal(kept.error.code, "invalid_cursor");
  assert.equal(recoveryFor(kept.error.code), "restart", "and the page offers a restart, not a retry");
});

// ---------------------------------------------------------------------------
// Loading / empty / error
// ---------------------------------------------------------------------------

test("U1-T06 the state machine distinguishes loading, empty, ready and each recovery", async () => {
  const fake = services();
  const notEmpty = (value: { items: unknown[] }) => value.items.length === 0;

  assert.deepEqual(viewStateOf(null, notEmpty), { kind: "loading" });

  const ready = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  assert.equal(viewStateOf(ready, notEmpty).kind, "ready");

  // A filter that matches nothing is empty, not an error and not a row of zeroes.
  const empty = await fake.usage(
    fake.sessions.owner,
    usagePageQuery(withFilter(WIDE, { model: "a-model-nobody-called" }), NOW),
  );
  assert.ok(empty.ok);
  assert.equal(empty.value.items.length, 0);
  assert.equal(viewStateOf(empty, notEmpty).kind, "empty");

  fake.failNext("usage", "dependency_unavailable");
  const broken = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  const state = viewStateOf(broken, notEmpty);
  assert.equal(state.kind, "error");
  assert.ok(state.kind === "error");
  assert.equal(state.code, "dependency_unavailable");
  assert.equal(state.recovery, "retry", "a 503 is worth retrying");
  assert.match(state.message, /unavailable/i);

  // The failure drill: the retry the page offers must actually work, and the failed read must have
  // left nothing behind. An injected failure fires before anything is read or written.
  const retried = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  assert.ok(retried.ok, "the retry succeeds");
  assert.ok(ready.ok);
  assert.deepEqual(retried.value, ready.value, "and returns exactly what the failed call would have");

  assert.equal(recoveryFor("invalid_cursor"), "restart");
  assert.equal(recoveryFor("rate_limited"), "retry");
  assert.equal(recoveryFor("internal_error"), "retry");
  assert.equal(recoveryFor("deadline_exceeded"), "retry");
  assert.equal(recoveryFor("forbidden"), "none", "a retry cannot grant authority");
  assert.equal(recoveryFor("invalid_request"), "none");
  assert.equal(recoveryFor("not_found"), "none");
  assert.equal(recoveryFor("org_suspended"), "none");

  assert.equal(explainError("dependency_unavailable", "raw"), explainError("dependency_unavailable", "other"));
  assert.equal(explainError("replay_gap", "the service message"), "the service message");
});

// ---------------------------------------------------------------------------
// Row display: a hold is not a charge
// ---------------------------------------------------------------------------

async function oneOfEach(): Promise<Map<SettlementState | "unsettled", UsageRow>> {
  const rows = await rowsOf(WIDE);
  const found = new Map<SettlementState | "unsettled", UsageRow>();
  for (const row of rows) {
    const key = row.settlement_state ?? "unsettled";
    if (!found.has(key)) found.set(key, row);
  }
  return found;
}

test("U1-T07 an unsettled amount is never shown as a charge, and a hold has its own column", async () => {
  const rows = await oneOfEach();
  for (const key of ["unsettled", "settled", "held_unknown", "released_free", "released_platform_absorbed"] as const) {
    assert.ok(rows.has(key), `the fixture must cover ${key}`);
  }

  const settled = rows.get("settled")!;
  assert.deepEqual(amountView(settled), {
    charged: displayMoney(settled.cost),
    held: null,
    final: true,
  });

  for (const key of ["unsettled", "held_unknown"] as const) {
    const row = rows.get(key)!;
    assert.ok(row.max_hold !== null, `a ${key} row carries an outstanding hold`);
    const view = amountView(row);
    assert.equal(view.charged, displayMoney(ZERO_MONEY), `${key}: nothing is charged yet`);
    assert.equal(view.held, displayMoney(row.max_hold), `${key}: the hold is reported as a hold`);
    assert.notEqual(view.held, view.charged, `${key}: a hold must not read as the charge`);
    assert.equal(view.final, false);
  }

  for (const key of ["released_free", "released_platform_absorbed"] as const) {
    const row = rows.get(key)!;
    const view = amountView(row);
    assert.equal(view.charged, displayMoney(ZERO_MONEY), `${key}: no charge`);
    assert.equal(view.held, null, `${key}: the hold was released`);
  }
});

test("U1-T08 unreported usage shows no token count and says so instead of estimating one", async () => {
  const rows = await oneOfEach();
  const unknown = rows.get("held_unknown")!;
  assert.equal(unknown.usage_certainty, "unknown");
  const view = tokensView(unknown);
  assert.equal(view.prompt, "—");
  assert.equal(view.completion, "—");
  assert.match(view.note ?? "", /not reported/i);

  // Counts the service does not vouch for are not counts. A row may carry numbers *and* say the
  // usage is unknown — the certainty is what decides, not whether the fields happen to be null.
  const reported = tokensView({
    usage_certainty: "unknown",
    prompt_tokens: 4096,
    completion_tokens: 128,
  });
  assert.equal(reported.prompt, "—", "unknown usage shows no count even when one was reported");
  assert.equal(reported.completion, "—");
  assert.match(reported.note ?? "", /not reported/i);

  const settled = rows.get("settled")!;
  assert.equal(settled.usage_certainty, "authoritative");
  const known = tokensView(settled);
  assert.equal(known.note, null);
  assert.equal(known.prompt, (settled.prompt_tokens ?? 0).toLocaleString("en-US"));
  assert.ok(/[0-9]/.test(known.completion));
});

test("U1-T09 each settlement state gets its own explanation", async () => {
  const rows = await oneOfEach();
  const labels = new Map<string, string>();
  for (const [key, row] of rows) {
    const view = settlementView(row);
    assert.ok(view.label.length > 0 && view.detail.length > 0, `${key} needs a label and a detail`);
    labels.set(key, view.label);
  }
  assert.equal(new Set(labels.values()).size, labels.size, "five facts, five labels");
  assert.equal(labels.get("settled"), "Settled");
  assert.equal(labels.get("unsettled"), "Not settled yet");
  assert.match(settlementView(rows.get("held_unknown")!).detail, /not a charge/i);
  assert.match(settlementView(rows.get("released_platform_absorbed")!).detail, /not charged/i);
  assert.equal(settlementView(rows.get("released_platform_absorbed")!).tone, "muted");

  const row = usageRowView(rows.get("unsettled")!);
  assert.equal(row.settlement.label, "Not settled yet");
  assert.equal(row.amount.charged, displayMoney(ZERO_MONEY));
  assert.equal(row.when, instantLabel(rows.get("unsettled")!.created_at));
});

test("U1-T10 the summary reports charges and holds as different figures, formatted as money", async () => {
  const fake = services();
  const summary = await fake.usageSummary(fake.sessions.owner, usageScopeQuery(WIDE, NOW));
  assert.ok(summary.ok);
  const tiles = summaryTiles(summary.value);
  const byLabel = new Map(tiles.map((tile) => [tile.label, tile]));

  assert.equal(byLabel.get("Charged")?.value, displayMoney(summary.value.cost));
  assert.equal(
    byLabel.get("Awaiting reconciliation")?.value,
    displayMoney(summary.value.pending_reconciliation),
  );
  assert.notEqual(
    summary.value.cost,
    summary.value.pending_reconciliation,
    "the fixture must make the two figures distinguishable",
  );
  assert.notEqual(byLabel.get("Charged")?.value, byLabel.get("Awaiting reconciliation")?.value);
  assert.match(byLabel.get("Awaiting reconciliation")?.hint ?? "", /not a charge/i);
  assert.match(byLabel.get("Charged")?.hint ?? "", /settled/i);
  assert.equal(byLabel.get("Requests")?.value, summary.value.requests.toLocaleString("en-US"));
  assert.equal(
    byLabel.get("Absorbed by the platform")?.value,
    summary.value.platform_absorbed_requests.toLocaleString("en-US"),
  );
  assert.ok(summary.value.platform_absorbed_requests > 0, "the fixture must contain absorbed failures");
});

test("U1-T11 money keeps all eight digits and timestamps are UTC whatever the locale", async () => {
  // A `Number` round-trip loses the eighth digit; the eighth digit is a real price here.
  assert.equal(displayMoney(parseMoney("0.00000001")), "$0.00000001");
  assert.equal(displayMoney(parseMoney("1234.5")), "$1,234.50");
  assert.equal(displayMoney(parseMoney("-0.00012345")), "-$0.00012345");

  const days = dayViews([
    { day: "2026-09-20", requests: 3, prompt_tokens: 1000, completion_tokens: 24, cost: parseMoney("0.00043210") },
  ]);
  assert.equal(days[0].cost, "$0.0004321");
  assert.equal(days[0].total, 1024);

  assert.equal(instantLabel("2026-09-20T12:34:56.789Z"), "2026-09-20 12:34 UTC");
  assert.equal(instantLabel("2026-01-02T00:00:00Z"), "2026-01-02 00:00 UTC");
});

test("U1-T12 the model filter offers the models on the page plus whatever is selected", async () => {
  const rows = await rowsOf(WIDE);
  const options = modelOptions(rows, null);
  assert.ok(options.length > 1, "the fixture serves more than one model");
  assert.deepEqual(options, [...options].sort(), "options are ordered");
  assert.equal(new Set(options).size, options.length, "and unique");

  const withSelection = modelOptions(rows, "a-model-not-in-this-page");
  assert.ok(
    withSelection.includes("a-model-not-in-this-page"),
    "the active filter must stay selectable even when no visible row uses it",
  );
});
