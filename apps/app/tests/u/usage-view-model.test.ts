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
import { readFileSync } from "node:fs";
import test from "node:test";

import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { displayMoney, parseMoney, ZERO_MONEY } from "../../lib/contracts/money.ts";
import {
  MAX_PAGE_LIMIT,
  USAGE_QUERY_FIELDS,
  type ApiKeySummary,
  type ErrorCode,
  type Page,
  type Result,
  type SettlementState,
  type UsageDay,
  type UsageRow,
} from "../../lib/contracts/types.ts";
import {
  ALL,
  DEFAULT_RANGE,
  MAX_CURSOR_CHARS,
  MAX_TRAIL_PAGES,
  PAGE_SIZE,
  RANGES,
  amountView,
  dayViews,
  explainError,
  firstCursorState,
  hasPreviousPage,
  instantLabel,
  keyFilterNotice,
  ledgerHref,
  mapState,
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
  usagePageModel,
  usagePageQuery,
  usageRowView,
  usageScopeQuery,
  usageSearch,
  viewStateOf,
  withFilter,
  type UsageFilters,
} from "../../app/(console)/usage/view-model.ts";
import {
  BOUNDARY_RECOVERY_PROP,
  boundaryCopy,
  type BoundaryScope,
} from "../../app/(console)/usage/boundary.ts";

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
  assert.deepEqual(
    parsePageCursor({ cursor: "c2", trail: "c1" }).trail,
    ["c1"],
    "one page back is not an array yet",
  );
  assert.deepEqual(parsePageCursor({ cursor: "", trail: ["", "c1"] }), { cursor: null, trail: [] });
});

test("U1-T13 a prototype key is not a range, and no URL value reaches a prototype lookup", () => {
  // `range in RANGES` was true for every Object.prototype member, so `?range=toString` made
  // `now - <function>` NaN and `new Date(NaN).toISOString()` threw: one query parameter, no page.
  for (const key of [
    "toString",
    "__proto__",
    "constructor",
    "hasOwnProperty",
    "valueOf",
    "isPrototypeOf",
    "propertyIsEnumerable",
    "toLocaleString",
  ]) {
    const filters = parseUsageFilters({ range: key });
    assert.equal(filters.range, DEFAULT_RANGE, `${key} must not pass as a range`);
    const query = usageScopeQuery(filters, NOW);
    assert.equal(query.to, NOW.toISOString());
    assert.ok(
      !Number.isNaN(Date.parse(query.from ?? "")),
      `${key} must not produce an unparseable from`,
    );
  }

  // The same guard at the other end: a range key from anywhere but the parser cannot make a NaN
  // instant either, because the one place that reads the table checks for an own property.
  const forged: UsageFilters = { ...WIDE, range: "__proto__" as unknown as UsageFilters["range"] };
  assert.doesNotThrow(
    () => usageScopeQuery(forged, NOW),
    "a forged range key must not throw: `RANGES.__proto__` is an object, and now - object is NaN",
  );
  assert.ok(!Number.isNaN(Date.parse(usageScopeQuery(forged, NOW).from ?? "")));

  // Error hints and HTTP statuses are looked up by code the same way.
  assert.equal(explainError("hasOwnProperty" as ErrorCode, "fallback"), "fallback");
  assert.equal(recoveryFor("toString" as ErrorCode), "none");
  assert.equal(recoveryFor("__proto__" as ErrorCode), "none");

  // And a settlement state outside the contract's vocabulary is a row that says so, not a crash.
  const unknown = settlementView({
    settlement_state: "not_a_state" as SettlementState,
    job_state: "succeeded",
    max_hold: null,
  });
  assert.ok(unknown, "an unknown settlement state must still produce a view, not undefined");
  assert.ok(unknown.detail, "with an explanation rather than a blank cell");
  assert.equal(unknown.label, "Unknown");
  assert.match(unknown.detail, /nothing is presented as charged/i);
});

test("U1-T14 a hand-written URL cannot grow the trail without bound", () => {
  const long = "c".repeat(MAX_CURSOR_CHARS + 1);
  assert.equal(parsePageCursor({ cursor: long }).cursor, null, "an over-long cursor is dropped");

  // The bound is inclusive: a real cursor of exactly the maximum length still works, or the cap
  // would quietly break the last page of a deep walk instead of a forged URL.
  const atLimit = "c".repeat(MAX_CURSOR_CHARS);
  assert.equal(parsePageCursor({ cursor: atLimit }).cursor, atLimit, "the limit itself is accepted");
  assert.deepEqual(
    parsePageCursor({ cursor: "c1", trail: [atLimit] }).trail,
    [atLimit],
    "and so is a trail entry of exactly the maximum length",
  );
  assert.equal(
    parsePageCursor({ cursor: "c1", trail: [long, "c0"] }).trail.length,
    1,
    "and so is an over-long trail entry",
  );

  const forged = Array.from({ length: MAX_TRAIL_PAGES + 500 }, (_, index) => `c${index}`);
  const parsed = parsePageCursor({ cursor: "last", trail: forged });
  assert.equal(parsed.trail.length, MAX_TRAIL_PAGES, "the trail is capped");
  assert.deepEqual(
    parsed.trail,
    forged.slice(-MAX_TRAIL_PAGES),
    "and it keeps the most recent pages",
  );

  // Page 1 has nothing behind it, so a trail without a cursor is noise or a forged back-stack.
  assert.deepEqual(parsePageCursor({ trail: forged }), { cursor: null, trail: [] });

  let state: UsageFilters = WIDE;
  for (let step = 0; step < MAX_TRAIL_PAGES + 10; step += 1) {
    state = nextCursorState(state, `c${step}`);
  }
  assert.equal(state.trail.length, MAX_TRAIL_PAGES, "and walking forward cannot grow it either");
  assert.ok(
    usageSearch(state).length < 16 * 1024,
    `a walked URL stays small, was ${usageSearch(state).length} characters`,
  );
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

  // Every unsettled fixture row happens to carry a zero cost, so the fixture alone cannot tell
  // "we read the cost only when settled" from "we read the cost always". A crafted row can: a
  // service that reports a cost beside an unreleased hold must still charge nothing on screen.
  const crafted = amountView({
    settlement_state: "held_unknown",
    cost: parseMoney("0.50000000"),
    max_hold: parseMoney("0.75000000"),
  });
  assert.equal(crafted.charged, "$0.00", "an unsettled row charges nothing, whatever its cost says");
  assert.equal(crafted.held, "$0.75", "and its hold is reported as a hold");
  assert.equal(crafted.final, false);
  assert.equal(
    amountView({
      settlement_state: null,
      cost: parseMoney("0.50000000"),
      max_hold: parseMoney("0.75000000"),
    }).charged,
    "$0.00",
    "and neither does a non-terminal one",
  );
  assert.equal(
    amountView({
      settlement_state: "settled",
      cost: parseMoney("0.50000000"),
      max_hold: null,
    }).charged,
    "$0.50",
    "a settled row does show its cost, or the column would be useless",
  );
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

  // One field missing is the same fact as both: a row that reports input but not output tells us
  // nothing we can put in a cell, and half a count is worse than none.
  for (const half of [
    { prompt_tokens: 4096, completion_tokens: null },
    { prompt_tokens: null, completion_tokens: 128 },
  ]) {
    const view = tokensView({ usage_certainty: "authoritative", ...half });
    assert.equal(view.prompt, "—", "a half-reported row shows no counts");
    assert.equal(view.completion, "—");
    assert.ok(view.note !== null);
  }

  // The mirror case: an authoritative row with nothing in the fields is also not a zero.
  const missing = tokensView({
    usage_certainty: "authoritative",
    prompt_tokens: null,
    completion_tokens: null,
  });
  assert.equal(missing.prompt, "—", "a missing count is not a count of zero");
  assert.equal(missing.completion, "—");
  assert.ok(missing.note !== null, "and it says why the cell is empty");

  const settled = rows.get("settled")!;
  assert.equal(settled.usage_certainty, "authoritative");
  const known = tokensView(settled);
  assert.equal(known.note, null);
  assert.equal(known.prompt, (settled.prompt_tokens ?? 0).toLocaleString("en-US"));
  assert.ok(/[0-9]/.test(known.completion));
});

test("U1-T29 a legacy row and a deleted key render as absences, never as invented values", async () => {
  const rows = await oneOfEach();
  const pilot = rows.get("settled")!;

  // r2: a row from before the pilot accounting regime carries no execution mode and no job state,
  // and a row whose key was deleted carries neither key field. The service reports those absences;
  // this layer is the only one allowed to turn them into text, and what it must never do is show a
  // value that no row contained — a mode of "sync" or a key name belonging to some other key.
  const legacy = usageRowView({
    ...pilot,
    accounting_regime: "legacy_usd",
    execution_mode: null,
    job_state: null,
    terminal_cause: null,
    usage_certainty: null,
    settlement_state: null,
    trace_mode: null,
    max_hold: null,
  });
  assert.equal(legacy.mode, "—", "no execution mode is an em dash, not a default mode");
  assert.equal(legacy.outcome, "—", "and no outcome is an em dash, not a job state it never had");
  // The columns that do have values still render, so the row is shown rather than dropped: a
  // legacy charge is real money and hiding it understates what the organization spent.
  assert.equal(legacy.requestId, pilot.request_id);
  assert.equal(legacy.when, instantLabel(pilot.created_at));
  assert.equal(legacy.model, pilot.model);

  const keyless = usageRowView({ ...pilot, key_id: null, key_name: null });
  assert.equal(keyless.keyName, "(deleted key)", "a deleted key is named as deleted");
  assert.equal(keyless.requestId, pilot.request_id, "and the row is still the organization's");
  // The label is a label: it is not the identifier of anything, so it can never be fed back as a
  // filter value that would match this row.
  assert.notEqual(keyless.keyName, pilot.key_id);

  // A pilot row with all its values renders none of the fallbacks, or the assertions above would
  // hold over a view that showed an em dash for everything.
  const full = usageRowView(pilot);
  assert.equal(full.mode, pilot.execution_mode);
  assert.equal(full.keyName, pilot.key_name);
  assert.notEqual(full.outcome, "—");
});

test("U1-T09 each settlement state gets its own label and its own explanation", async () => {
  const rows = await oneOfEach();
  const labels = new Map<string, string>();
  const details = new Map<string, string>();
  for (const [key, row] of rows) {
    const view = settlementView(row);
    assert.ok(view.label.length > 0 && view.detail.length > 0, `${key} needs a label and a detail`);
    labels.set(key, view.label);
    details.set(key, view.detail);
  }
  assert.equal(new Set(labels.values()).size, labels.size, "five facts, five labels");
  // The label is the badge; the sentence under it is what a customer actually reads. Giving a free
  // failure the platform-absorbed explanation (or the reverse) is wrong in exactly the way that
  // matters, and identical labels would not catch it.
  assert.equal(new Set(details.values()).size, details.size, "five facts, five explanations");

  assert.equal(labels.get("settled"), "Settled");
  assert.equal(labels.get("unsettled"), "Not settled yet");
  assert.equal(labels.get("held_unknown"), "Awaiting reconciliation");
  assert.equal(labels.get("released_free"), "No charge");
  assert.equal(labels.get("released_platform_absorbed"), "Platform absorbed");

  assert.match(details.get("held_unknown") ?? "", /not a charge/i);
  assert.match(details.get("released_platform_absorbed") ?? "", /the failure was ours/i);
  assert.match(details.get("released_platform_absorbed") ?? "", /not charged/i);
  assert.match(details.get("released_free") ?? "", /before it produced billable work/i);
  assert.match(details.get("settled") ?? "", /priced and drawn/i);
  assert.equal(settlementView(rows.get("released_platform_absorbed")!).tone, "muted");

  // An unsettled row must never claim a charge. This is the sentence beside a request that is still
  // running, so "This has been charged." would be a lie the badge does not contradict.
  const unsettled = details.get("unsettled") ?? "";
  assert.match(unsettled, /nothing has been charged/i);
  assert.match(unsettled, /ceiling rather than a price/i);
  assert.equal(settlementView(rows.get("unsettled")!).tone, "neutral");

  const row = usageRowView(rows.get("unsettled")!);
  assert.equal(row.settlement.label, "Not settled yet");
  assert.equal(row.amount.charged, displayMoney(ZERO_MONEY));
  assert.equal(row.when, instantLabel(rows.get("unsettled")!.created_at));
  // The outcome column names the terminal cause where there is one, because "failed" alone does not
  // tell a customer whether it was their media or our engine.
  assert.equal(usageRowView(rows.get("settled")!).outcome, rows.get("settled")!.terminal_cause);
  assert.equal(
    row.outcome,
    rows.get("unsettled")!.job_state,
    "and falls back to the job state only while there is no cause",
  );
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

// ---------------------------------------------------------------------------
// The page model: a failed read is never an empty screen
// ---------------------------------------------------------------------------

const FAILED = <T,>(code: ErrorCode = "dependency_unavailable"): Result<T> => ({
  ok: false,
  error: { code, message: "injected failure" },
});

async function pageInput(filters: UsageFilters = WIDE) {
  const fake = services();
  const session = fake.sessions.owner;
  const [usage, summary, daily, keys] = await Promise.all([
    fake.usage(session, usagePageQuery(filters, NOW)),
    fake.usageSummary(session, usageScopeQuery(filters, NOW)),
    fake.usageDaily(session, usageScopeQuery(filters, NOW)),
    fake.keys.list(session),
  ]);
  return { filters, usage, summary, daily, keys };
}

test("U1-T15 every failed read on the usage page is an error state, never an empty one", async () => {
  const input = await pageInput();
  const healthy = usagePageModel(input);
  for (const part of ["summary", "daily", "rows", "keys"] as const) {
    assert.equal(healthy[part].kind, "ready", `${part} must be ready when the service answers`);
  }
  assert.equal(healthy.keyNotice, null);
  assert.ok(healthy.rows.kind === "ready" && healthy.rows.value.rows.length === PAGE_SIZE);
  assert.ok(healthy.keyOptions.length > 0, "the key filter has options");

  // One failure at a time: each part must fail on its own and take nothing else down with it.
  for (const part of ["summary", "daily", "usage", "keys"] as const) {
    const model = usagePageModel({ ...input, [part]: FAILED() });
    const state = part === "usage" ? model.rows : model[part];
    assert.equal(state.kind, "error", `a failed ${part} read must be an error state`);
    assert.ok(state.kind === "error");
    assert.equal(state.code, "dependency_unavailable");
    assert.equal(state.recovery, "retry", "and it must offer the retry it can recover with");
    assert.notEqual(state.kind, "empty", `a failed ${part} read must never read as "nothing here"`);
    for (const other of ["summary", "daily", "rows", "keys"] as const) {
      if ((part === "usage" ? "rows" : part) === other) continue;
      assert.equal(model[other].kind, "ready", `a failed ${part} must leave ${other} alone`);
    }
  }

  // An empty *successful* read is still empty — the distinction the error states exist to keep.
  const emptyDaily: Result<UsageDay[]> = { ok: true, value: [] };
  assert.equal(usagePageModel({ ...input, daily: emptyDaily }).daily.kind, "empty");
  const emptyKeys: Result<ApiKeySummary[]> = { ok: true, value: [] };
  assert.equal(usagePageModel({ ...input, keys: emptyKeys }).keys.kind, "empty");
  const emptyUsage: Result<Page<UsageRow>> = { ok: true, value: { items: [], next_cursor: null } };
  assert.equal(usagePageModel({ ...input, usage: emptyUsage }).rows.kind, "empty");

  assert.deepEqual(mapState({ kind: "loading" }, () => "mapped"), { kind: "loading" });
  assert.deepEqual(mapState({ kind: "ready", value: 1 }, (value) => value + 1), {
    kind: "ready",
    value: 2,
  });
});

test("U1-T16 the page model computes every href and page number the markup renders", async () => {
  const fake = services();
  const first = await fake.usage(fake.sessions.owner, usagePageQuery(WIDE, NOW));
  assert.ok(first.ok && first.value.next_cursor !== null);
  const second = nextCursorState(WIDE, first.value.next_cursor);

  const model = usagePageModel(await pageInput(second));
  assert.ok(model.rows.kind === "ready");
  assert.equal(model.rows.value.page, 2);
  assert.equal(model.rows.value.firstHref, usageHref(firstCursorState(second)));
  assert.equal(model.rows.value.previousHref, usageHref(previousCursorState(second)));
  assert.ok(model.rows.value.nextHref !== null, "there is a page 3 in the fixture");
  assert.equal(model.here, usageHref(second), "the retry target is the URL being shown");

  const onlyPage = usagePageModel({
    ...(await pageInput()),
    usage: { ok: true, value: { items: [], next_cursor: null } },
  });
  assert.equal(onlyPage.rows.kind, "empty", "and an empty page has no pager at all");

  const firstModel = usagePageModel(await pageInput(WIDE));
  assert.ok(firstModel.rows.kind === "ready");
  assert.equal(firstModel.rows.value.previousHref, null, "page 1 has no Previous");
  assert.equal(firstModel.rows.value.page, 1);

  // The last page has no Next. Walk to the end and check the edge, because "there is always a Next"
  // is the pagination bug a reader finds by clicking into an empty page.
  let last = WIDE;
  for (let guard = 0; guard < 50; guard += 1) {
    const page = await fake.usage(fake.sessions.owner, usagePageQuery(last, NOW));
    assert.ok(page.ok);
    if (page.value.next_cursor === null) break;
    last = nextCursorState(last, page.value.next_cursor);
  }
  const lastModel = usagePageModel(await pageInput(last));
  assert.ok(lastModel.rows.kind === "ready");
  assert.equal(lastModel.rows.value.nextHref, null, "the last page offers no Next");
  assert.ok(lastModel.rows.value.previousHref !== null, "but it can still go back");
  assert.ok(lastModel.rows.value.page > 1);
  assert.ok(
    lastModel.here.includes("cursor="),
    "and the retry target carries the cursor of the page being shown",
  );

  // Clearing the key filter is a link the notice can point at. It clears the key and resets the walk
  // — and keeps every filter it was not asked to clear, or "clear the key filter" would silently
  // widen the range and the model too.
  const filtered = usagePageModel(
    await pageInput({ ...second, range: "7d", keyId: "some-key", model: "marlin-2b@2026-09-01" }),
  );
  assert.ok(filtered.clearKeyFilterHref !== null);
  assert.ok(
    !filtered.clearKeyFilterHref.includes("key=") && !filtered.clearKeyFilterHref.includes("cursor="),
    "clearing a filter drops the filter and the cursor",
  );
  assert.ok(!filtered.clearKeyFilterHref.includes("trail="), "and the trail with it");
  assert.deepEqual(
    parseUsageFilters(
      Object.fromEntries(new URLSearchParams(filtered.clearKeyFilterHref.split("?")[1] ?? "")),
    ),
    { range: "7d", keyId: null, model: "marlin-2b@2026-09-01", cursor: null, trail: [] },
    "the range and the model survive clearing the key",
  );
  assert.equal(usagePageModel(await pageInput(WIDE)).clearKeyFilterHref, null, "nothing to clear");
});

test("U1-T19 the error boundaries wire up the recovery that can actually recover", () => {
  // Next 16.3.5's shipped docs: `retry()` re-fetches and re-renders the boundary's children, while
  // `reset()` re-renders them *without* re-fetching and "in most cases, you should use retry()".
  // Both pages are Server Components, so a caught throw happened on the server and `reset()` replays
  // the same errored payload — a "Try again" button that can never work.
  assert.equal(BOUNDARY_RECOVERY_PROP, "retry");

  for (const file of ["usage/error.tsx", "billing/error.tsx"]) {
    const source = readFileSync(new URL(`../../app/(console)/${file}`, import.meta.url), "utf8");
    assert.match(source, /^"use client";/m, `${file}: an error boundary is a Client Component`);
    assert.match(source, /\{\s*(?:error,\s*)?retry\s*\}/, `${file}: must destructure ${BOUNDARY_RECOVERY_PROP}`);
    assert.match(source, /onClick=\{\(\) => retry\(\)\}/, `${file}: must call retry() on click`);
    assert.doesNotMatch(
      source.replace(/`reset\(\)`/g, "").replace(/^\s*\*.*$/gm, ""),
      /reset/,
      `${file}: reset() cannot recover a Server Component throw`,
    );
    // The thrown error's own text never reaches the page: it can carry internals. Checking for the
    // literal `{error.message}` only catches the spelling we happened to think of, so strip the
    // comments and the props type and require that the word does not appear in the code at all.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    // I3 (I3R-6): the one other use is handing it to the report hook, which sends no message.
    const withoutPropsType = code
      .replace(/\{\s*error: Error & \{ digest\?: string \};\s*retry: \(\) => void\s*\}/g, "{ /* props type */ }")
      .replace("{ error, retry }", "{ retry }")
      .replace("useErrorReport(error);", "")
      .replace('from "@/lib/deploy/error-view"', "");
    assert.match(code, /error: Error & \{ digest\?: string \}/, `${file}: still declares the prop`);
    assert.doesNotMatch(
      withoutPropsType,
      /\berror\b/,
      `${file}: the thrown error must not be referenced outside the props type`,
    );
  }

  for (const scope of ["usage", "balance"] as const) {
    const copy = boundaryCopy(scope);
    assert.equal(copy.action, "Try again");
    assert.ok(copy.headline.length > 0 && copy.detail.length > 0);
    assert.match(copy.detail, /unaffected/i, "the reader is told nothing was charged by the failure");
    assert.doesNotMatch(
      `${copy.headline} ${copy.detail} ${copy.action}`,
      /add credits|add card|top ?up|\bbuy\b|purchase|invoice|checkout|revenue|pay now|credit card/i,
    );
  }
  assert.notEqual(boundaryCopy("usage").detail, boundaryCopy("balance").detail, "each route says what it is");

  // An unrecognised scope falls back to the usage copy exactly. Asserted as an equality, because
  // without the `Object.hasOwn` guard the lookup yields `Object.prototype` and interpolates
  // "[object Object]" — a string that contains neither "undefined" nor anything else a loose check
  // would notice.
  for (const weird of ["__proto__", "constructor", "toString", "nope"]) {
    assert.deepEqual(
      boundaryCopy(weird as BoundaryScope),
      boundaryCopy("usage"),
      `${weird} must fall back to the usage copy, whole`,
    );
    assert.doesNotMatch(boundaryCopy(weird as BoundaryScope).detail, /object Object|undefined/);
  }
});

test("U1-T17 a key filter naming a key of another organization is explained, not shown as silence", async () => {
  const fake = services();
  const keys = await fake.keys.list(fake.sessions.owner);
  assert.ok(keys.ok);

  const mine = keys.value[0].id;
  assert.equal(keyFilterNotice({ ...WIDE, keyId: mine }, keys), null, "my own key is fine");
  assert.equal(keyFilterNotice(WIDE, keys), null, "and so is no filter at all");

  // The service answers an unknown key with an ok, empty page — which on screen reads as "you have
  // no traffic". Only the organization's own key list can tell the two apart.
  const foreign = { ...WIDE, keyId: fake.ids.otherOrgKeyId };
  const page = await fake.usage(fake.sessions.owner, usagePageQuery(foreign, NOW));
  assert.ok(page.ok, "the service does not refuse an unknown key");
  assert.equal(page.value.items.length, 0, "it returns nothing at all");

  const notice = keyFilterNotice(foreign, keys);
  assert.ok(notice !== null, "so the page must say the filter is the reason");
  assert.match(notice, /does not belong to this organization/i);
  assert.match(notice, /clear the key filter/i);

  const model = usagePageModel({ ...(await pageInput(foreign)), keys });
  assert.equal(model.rows.kind, "empty");
  assert.equal(model.keyNotice, notice, "the page model carries it");

  // With the key list itself unreadable there is nothing to check against, and a guess would be
  // worse than silence: the key-list error is what the page shows instead.
  assert.equal(keyFilterNotice(foreign, FAILED<ApiKeySummary[]>()), null);
  assert.equal(usagePageModel({ ...(await pageInput(foreign)), keys: FAILED() }).keys.kind, "error");
});

test("U1-T18 no usage-page string offers payment or calls promotional credit revenue", async () => {
  const fake = services();
  const summary = await fake.usageSummary(fake.sessions.owner, usageScopeQuery(WIDE, NOW));
  assert.ok(summary.ok);
  const rows = await oneOfEach();

  const strings = [
    ...summaryTiles(summary.value).flatMap((tile) => [tile.label, tile.hint]),
    ...[...rows.values()].flatMap((row) => {
      const view = usageRowView(row);
      return [view.settlement.label, view.settlement.detail, view.tokens.note ?? ""];
    }),
    ...(
      [
        "invalid_cursor",
        "invalid_request",
        "forbidden",
        "not_found",
        "org_suspended",
        "rate_limited",
        "dependency_unavailable",
        "internal_error",
        "deadline_exceeded",
      ] as const
    ).map((code) => explainError(code, "")),
    keyFilterNotice({ ...WIDE, keyId: "nope" }, await fake.keys.list(fake.sessions.owner)) ?? "",
  ];

  for (const value of strings) {
    assert.doesNotMatch(
      value,
      /add credits|add card|top ?up|\bbuy\b|purchase|invoice|checkout|revenue|pay now|credit card/i,
      `payment or revenue wording in usage copy: ${value}`,
    );
  }
  assert.ok(strings.filter((value) => value.length > 0).length > 20, "the scan must cover the copy");
});
