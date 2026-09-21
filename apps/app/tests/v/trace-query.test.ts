// node --test "tests/**/*.test.ts"
//
// V1 — URL state for the trace list (oracles CONSOLE-FLOWS, TRACE-TENANT).
//
// What these cases are for: the page must never hand a query parameter to `ConsoleServices.traces`
// because it was in the URL. Every value is either inside its vocabulary or refused and reported,
// the window a walk uses is pinned so its cursors stay valid, and the query that comes out is one
// the contract accepts — which the last case proves against the fake rather than by assertion.
import assert from "node:assert/strict";
import test from "node:test";

import {
  CONTENT_FILTERS,
  DEFAULT_RANGE,
  PAGE_SIZES,
  parseTraceParams,
  traceHref,
  withPatch,
  type FilterState,
  type RawParams,
} from "../../app/(console)/traces/query.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import {
  DEFAULT_PAGE_LIMIT,
  MAX_PAGE_LIMIT,
  TRACE_QUERY_FIELDS,
  type TraceListItem,
} from "../../lib/contracts/types.ts";

/** A fixed instant, so every window in these cases is exact rather than "about now". */
const NOW = Date.parse("2026-09-21T12:00:00.000Z");
const OWN_KEYS = ["a1000000-0000-4000-8000-000000000001", "a1000000-0000-4000-8000-000000000002"];

function parse(raw: RawParams, keyIds: readonly string[] = OWN_KEYS) {
  return parseTraceParams(raw, { now: NOW, keyIds });
}

function names(rejected: { name: string }[]): string[] {
  return rejected.map((entry) => entry.name).sort();
}

test("V1-Q01 only the parameters this page declares reach the typed query", () => {
  const parsed = parse({
    // Nothing here is a parameter of this page. Three of them are *contract* field names, which is
    // the dangerous case: forwarded, they would be a raw query passthrough into the service.
    org_id: "22222222-2222-4222-8222-222222222222",
    author_role: "operator",
    limit: "1000",
    has_feedback: "true",
    from: "1970-01-01T00:00:00Z",
    job_state: "succeeded",
    storage_key: "traces/2026/09/21/x.json.zst",
  });

  assert.deepEqual(parsed.ignored, [
    "author_role",
    "from",
    "has_feedback",
    "job_state",
    "limit",
    "org_id",
    "storage_key",
  ]);
  assert.deepEqual(parsed.rejected, [], "an unknown name is ignored, not a rejected value");
  // The query is built from the parsed filters only: every key is a contract field, the limit is
  // this page's, and no smuggled value survives anywhere in it.
  for (const key of Object.keys(parsed.query)) {
    assert.ok(
      (TRACE_QUERY_FIELDS as readonly string[]).includes(key),
      `${key} is not a field of TraceQuery`,
    );
  }
  assert.equal(parsed.query.limit, DEFAULT_PAGE_LIMIT);
  assert.equal(parsed.query.has_feedback, undefined);
  assert.equal(parsed.query.job_state, undefined);
  assert.equal(parsed.query.from, "2026-09-20T12:00:00.000Z", "the window comes from range, not from");
  const serialized = JSON.stringify(parsed.query);
  for (const smuggled of ["22222222", "operator", "storage_key", "1970-01-01"]) {
    assert.ok(!serialized.includes(smuggled), `${smuggled} reached the query`);
  }
});

test("V1-Q02 a filter value outside its vocabulary is refused and the default stands", () => {
  const parsed = parse({
    range: "99y",
    state: "finished",
    content: "partial",
    mode: "metadata",
    feedback: "maybe",
  });
  assert.deepEqual(names(parsed.rejected), ["content", "feedback", "mode", "range", "state"]);
  assert.equal(parsed.filters.range, DEFAULT_RANGE);
  assert.equal(parsed.filters.state, null);
  assert.equal(parsed.filters.content, null);
  assert.equal(parsed.filters.mode, null);
  assert.equal(parsed.filters.feedback, "any");
  assert.deepEqual(Object.keys(parsed.query).sort(), ["from", "limit", "to"]);
  // `metadata` is 07-console-spec's old name for `minimal` (R13). It must be refused, not mapped,
  // or a stale link silently filters by something the reader did not ask for.
  assert.ok(parsed.rejected.some((entry) => entry.name === "mode"));
});

test("V1-Q03 `off` is not a list filter, and the refusal says where those requests are", () => {
  const content = parse({ content: "off" });
  assert.equal(content.filters.content, null);
  assert.equal(content.query.content, undefined, "an off filter would return an empty page");
  const why = content.rejected.find((entry) => entry.name === "content")?.why ?? "";
  assert.match(why, /Usage/, "the reader is told where off-mode requests are listed");
  assert.ok(!(CONTENT_FILTERS as readonly string[]).includes("off"));

  const mode = parse({ mode: "off" });
  assert.equal(mode.filters.mode, null);
  assert.equal(mode.query.trace_mode, undefined);
  assert.equal(names(mode.rejected).length, 1);

  // The five states that *can* appear in a list row are all accepted.
  for (const state of CONTENT_FILTERS) {
    const parsed = parse({ content: state });
    assert.deepEqual(parsed.rejected, [], `${state} must be an accepted filter`);
    assert.equal(parsed.query.content, state);
  }
});

test("V1-Q04 a key filter must name one of this organization's keys", () => {
  const foreign = parse({ key: "b1000000-0000-4000-8000-000000000001" });
  assert.equal(foreign.filters.key, null);
  assert.equal(foreign.query.key_id, undefined, "a guessed key id never reaches the service");
  assert.match(foreign.rejected[0].why, /this organization's keys/);

  const own = parse({ key: OWN_KEYS[1] });
  assert.deepEqual(own.rejected, []);
  assert.equal(own.query.key_id, OWN_KEYS[1]);

  // A model has no console catalogue to check against, so it is bounded and checked for shape only.
  const long = parse({ model: "m".repeat(201) });
  assert.equal(long.query.model, undefined);
  assert.equal(names(long.rejected)[0], "model");
  const control = parse({ model: "marlin-2b\u0000" });
  assert.equal(control.query.model, undefined);
  assert.match(control.rejected[0].why, /control characters/);
  const fine = parse({ model: "marlin-2b@2026-09-01" });
  assert.deepEqual(fine.rejected, []);
  assert.equal(fine.query.model, "marlin-2b@2026-09-01");
});

test("V1-Q05 the window is resolved from the range, and a page link pins it", () => {
  const relative = parse({ range: "7d" });
  assert.equal(relative.filters.pinned, false);
  assert.equal(relative.query.to, "2026-09-21T12:00:00.000Z");
  assert.equal(relative.query.from, "2026-09-14T12:00:00.000Z");

  // Pinned: the same URL resolves to the same window at any later clock, which is what keeps a
  // cursor minted for that window valid for the whole walk.
  const pinnedRaw = { range: "7d", at: "2026-09-20T00:00:00.000Z", cursor: "Y3Vyc29yLTE=" };
  const pinned = parse(pinnedRaw);
  const later = parseTraceParams(pinnedRaw, { now: NOW + 86_400_000, keyIds: OWN_KEYS });
  assert.equal(pinned.filters.pinned, true);
  assert.deepEqual(
    [pinned.query.from, pinned.query.to],
    [later.query.from, later.query.to],
    "a pinned window does not move with the clock",
  );
  assert.equal(pinned.query.cursor, "Y3Vyc29yLTE=", "an opaque cursor is passed through verbatim");

  // Without the anchor the window would slide under the walk, so the service would answer
  // `invalid_cursor` on the second page. The cursor is refused here instead, with the reason.
  const unpinned = parse({ range: "7d", cursor: "Y3Vyc29yLTE=" });
  assert.equal(unpinned.query.cursor, undefined);
  assert.match(unpinned.rejected[0].why, /pinned window/);

  const badAnchor = parse({ at: "2026-09-20 00:00:00" });
  assert.equal(badAnchor.filters.pinned, false);
  assert.deepEqual(names(badAnchor.rejected), ["at"]);
});

test("V1-Q06 a cursor is bounded and charset-checked, and otherwise opaque", () => {
  const huge = parse({ at: "2026-09-20T00:00:00.000Z", cursor: "a".repeat(1025) });
  assert.equal(huge.query.cursor, undefined);
  assert.equal(names(huge.rejected)[0], "cursor");

  const hostile = parse({ at: "2026-09-20T00:00:00.000Z", cursor: "abc def'--" });
  assert.equal(hostile.query.cursor, undefined);

  // A signed or encrypted cursor (what C will mint) must survive untouched.
  const signed = "eyJhIjoiMjAyNi0wNi0yNSJ9.c2ln-ok_9~+/=";
  const ok = parse({ at: "2026-09-20T00:00:00.000Z", cursor: signed });
  assert.deepEqual(ok.rejected, []);
  assert.equal(ok.query.cursor, signed);
});

test("V1-Q07 the page size comes from the offered set, and is never a clamp", () => {
  for (const size of PAGE_SIZES) {
    const parsed = parse({ size: String(size) });
    assert.deepEqual(parsed.rejected, []);
    assert.equal(parsed.query.limit, size);
    assert.ok(size <= MAX_PAGE_LIMIT, "every offered size is inside the contract's hard bound");
  }
  for (const bad of ["1000", "101", "0", "-25", "25.5", "abc", "1e2"]) {
    const parsed = parse({ size: bad });
    assert.equal(parsed.query.limit, DEFAULT_PAGE_LIMIT, `size=${bad} must not become a limit`);
    assert.equal(names(parsed.rejected)[0], "size", `size=${bad} must be reported`);
  }
});

test("V1-Q08 a repeated parameter is refused rather than silently resolved", () => {
  const parsed = parse({ state: ["succeeded", "failed"], range: ["1h", "7d"] });
  assert.deepEqual(names(parsed.rejected), ["range", "state"]);
  assert.equal(parsed.filters.state, null);
  assert.equal(parsed.filters.range, DEFAULT_RANGE);
});

test("V1-Q09 changing a filter drops the cursor; a page link keeps the window", () => {
  const base = parse({ range: "7d", at: "2026-09-20T00:00:00.000Z", cursor: "Y3Vy" }).filters;

  const paged = traceHref(base, { cursor: "bmV4dA==" });
  assert.match(paged, /cursor=bmV4dA%3D%3D/);
  assert.match(paged, /at=2026-09-20T00%3A00%3A00.000Z/, "a page link pins the window");

  // Any filter change starts a new walk: keeping the cursor would resume one that no longer exists.
  for (const patch of [
    { state: "failed" as const },
    { content: "lost" as const },
    { mode: "full" as const },
    { key: OWN_KEYS[0] },
    { model: "marlin-2b@2026-09-01" },
    { feedback: "yes" as const },
    { range: "1h" as const },
    { size: 50 },
  ] satisfies Partial<FilterState>[]) {
    const href = traceHref(base, patch);
    assert.ok(!href.includes("cursor="), `${JSON.stringify(patch)} must drop the cursor: ${href}`);
    assert.ok(!href.includes("at="), `${JSON.stringify(patch)} must release the pinned window`);
    assert.equal(withPatch(base, patch).cursor, null);
  }

  // The same value is not a change, so a re-selected filter does not throw away the walk.
  assert.match(traceHref(base, { range: "7d" }), /cursor=/);
  // Defaults are left out, so a cleared list has a clean shareable URL.
  assert.equal(traceHref(base, { range: DEFAULT_RANGE, feedback: "any" }), "/traces");
});

test("V1-Q10 every query this page builds is one the service accepts, and its walk is stable", async () => {
  const services = createFakeConsoleServices();
  const session = services.sessions.owner;
  const keysResult = await services.keys.list(session);
  assert.ok(keysResult.ok);
  const keyIds = keysResult.value.map((key) => key.id);
  // The fixture clock sits in the past relative to any real `now`, so these cases anchor the window
  // on the newest row and ask for a range wide enough to contain the fixtures.
  const first = await services.traces(session, { limit: 1 });
  assert.ok(first.ok);
  const anchor = first.value.items[0].created_at;

  const filterSets: RawParams[] = [
    {},
    { range: "30d", at: anchor },
    { range: "30d", at: anchor, state: "succeeded" },
    { range: "30d", at: anchor, content: "lost" },
    { range: "30d", at: anchor, mode: "minimal" },
    { range: "30d", at: anchor, feedback: "yes" },
    { range: "30d", at: anchor, feedback: "no" },
    { range: "30d", at: anchor, key: keyIds[0], model: "marlin-2b@2026-09-01", size: "50" },
    // Hostile values alongside good ones: the good ones still work, nothing else is forwarded.
    { range: "30d", at: anchor, content: "off", limit: "1000", org_id: "x", size: "999" },
  ];
  for (const raw of filterSets) {
    const parsed = parseTraceParams(raw, { now: NOW, keyIds });
    const result = await services.traces(session, parsed.query);
    assert.ok(
      result.ok,
      `the service refused a query this page built from ${JSON.stringify(raw)}: ${
        result.ok ? "" : result.error.code
      }`,
    );
  }

  /** Walk the list the way a reader does: follow the page link, re-parse it, ask again. */
  async function walk(size: number): Promise<string[]> {
    let href = traceHref(parseTraceParams({ range: "30d", at: anchor, size: String(size) }, { now: NOW, keyIds }).filters);
    const seen: string[] = [];
    for (let page = 0; page < 20; page += 1) {
      const raw = Object.fromEntries(new URL(href, "https://console.invalid").searchParams);
      const parsed = parseTraceParams(raw, { now: NOW + page * 60_000, keyIds });
      const result = await services.traces(session, parsed.query);
      assert.ok(result.ok, `page ${page} was refused: ${result.ok ? "" : result.error.code}`);
      const items: TraceListItem[] = result.value.items;
      seen.push(...items.map((item) => item.request_id));
      if (result.value.next_cursor === null) return seen;
      href = traceHref(parsed.filters, { cursor: result.value.next_cursor });
    }
    throw new Error("the walk did not terminate");
  }

  const small = await walk(25);
  const large = await walk(100);
  assert.ok(small.length > DEFAULT_PAGE_LIMIT, `expected more than one page, walked ${small.length}`);
  assert.deepEqual(small, large, "the same rows in the same order at two page sizes");
  assert.equal(new Set(small).size, small.length, "no row is returned twice across the walk");
});
