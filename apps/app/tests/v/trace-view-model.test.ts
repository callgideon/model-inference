// node --test "tests/**/*.test.ts"
//
// V1 — the trace list view model (oracles CONSOLE-FLOWS, TRACE-TENANT).
//
// The invariant these cases exist for: **content availability is five distinct states and only one
// of them is a failure.** A page that draws "metadata only", "expired" or an absent off-mode request
// the way it draws a lost capture tells the reader their traces are broken when they are not — and
// one that draws a lost capture quietly tells them nothing was lost. Everything else here is the
// loading/empty/error/retry surface and the page-scoped lag and loss indicators.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { parseTraceParams, traceHref } from "../../app/(console)/traces/query.ts";
import {
  buildTraceListView,
  presentContent,
  statusTone,
  type ContentTone,
} from "../../app/(console)/traces/view-model.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { parseMoney } from "../../lib/contracts/money.ts";
import {
  TRACE_CONTENT_AVAILABILITY,
  TRACE_LOSS_REASONS,
  type ApiKeySummary,
  type ErrorCode,
  type Page,
  type Result,
  type TraceContentAvailability,
  type TraceListItem,
  type TraceLossReason,
} from "../../lib/contracts/types.ts";

const NOW = Date.parse("2026-09-21T12:00:00.000Z");
const PARSED_IDLE = parseTraceParams({}, { now: NOW, keyIds: [] });
const PARSED_NARROWED = parseTraceParams({ state: "failed" }, { now: NOW, keyIds: [] });
const FILTERS = PARSED_IDLE.filters;
const NARROWED = PARSED_NARROWED.filters;

function row(over: Partial<TraceListItem> = {}): TraceListItem {
  return {
    request_id: "00000000-0000-4000-8000-000000000001",
    created_at: "2026-09-21T11:00:00.000Z",
    model: "marlin-2b@2026-09-01",
    key_id: "a1000000-0000-4000-8000-000000000001",
    job_state: "succeeded",
    http_status: 200,
    trace_mode: "full",
    content: "available",
    loss_reason: "none",
    prompt_tokens: 120,
    completion_tokens: 40,
    ttft_ms: 180,
    wall_ms: 1200,
    cost: parseMoney("0.00012340"),
    feedback_count: 0,
    score_count: 0,
    ...over,
  };
}

function key(over: Partial<ApiKeySummary> = {}): ApiKeySummary {
  return {
    id: "a1000000-0000-4000-8000-000000000001",
    name: "production",
    prefix: "sk-infrx-prod",
    created_at: "2026-09-01T00:00:00.000Z",
    last_used_at: null,
    revoked_at: null,
    trace_mode: "full",
    ...over,
  };
}

function page(items: TraceListItem[], nextCursor: string | null = null): Result<Page<TraceListItem>> {
  return { ok: true, value: { items, next_cursor: nextCursor } };
}

function failure(code: ErrorCode): Result<Page<TraceListItem>> {
  return { ok: false, error: { code, message: "safe message" } };
}

test("V1-V01 every content state is its own state, and only a lost capture is a failure", () => {
  // The whole table, tone included: `pending` sharing `lost`'s destructive tone would tell a reader
  // whose projection is merely a few seconds behind that their content is gone.
  const expected: Record<
    TraceContentAvailability,
    { label: string; tone: ContentTone; failed: boolean; unexpected: boolean }
  > = {
    available: { label: "Full", tone: "ok", failed: false, unexpected: false },
    metadata_only: { label: "Metadata only", tone: "muted", failed: false, unexpected: false },
    pending: { label: "Pending", tone: "waiting", failed: false, unexpected: false },
    lost: { label: "Lost", tone: "warn", failed: true, unexpected: false },
    expired: { label: "Expired", tone: "muted", failed: false, unexpected: false },
    // An off-mode request has no trace row (R13). If one ever reaches a list it is a projection
    // defect, not a capture failure, and it must not be drawn as one.
    off: { label: "Not captured", tone: "muted", failed: false, unexpected: true },
  };
  const labels = new Set<string>();
  for (const state of TRACE_CONTENT_AVAILABILITY) {
    const shown = presentContent({ content: state, loss_reason: "memory_budget" });
    assert.equal(shown.state, state);
    assert.equal(shown.label, expected[state].label, `${state} label`);
    assert.equal(shown.tone, expected[state].tone, `${state} tone`);
    assert.equal(shown.failed, expected[state].failed, `${state} failed flag`);
    assert.equal(shown.unexpected, expected[state].unexpected, `${state} unexpected flag`);
    assert.ok(shown.detail.length > 0, `${state} needs an explanation`);
    labels.add(shown.label);
    if (!expected[state].failed) {
      assert.equal(shown.reason, null, `${state} must not borrow a loss reason`);
    }
  }
  assert.equal(labels.size, TRACE_CONTENT_AVAILABILITY.length, "each state reads differently");
  // Only the failure may carry the alarming tone.
  const alarming = TRACE_CONTENT_AVAILABILITY.filter(
    (state) => presentContent({ content: state, loss_reason: "none" }).tone === "warn",
  );
  assert.deepEqual(alarming, ["lost"]);
  // Two states that are routinely mistaken for failures:
  assert.equal(presentContent({ content: "metadata_only", loss_reason: "none" }).failed, false);
  assert.equal(presentContent({ content: "expired", loss_reason: "none" }).failed, false);
});

test("V1-V02 a lost row names its reason, and a lost row with no reason says exactly that", () => {
  const details = new Set<string>();
  for (const reason of TRACE_LOSS_REASONS) {
    const shown = presentContent({ content: "lost", loss_reason: reason });
    assert.equal(shown.failed, true);
    assert.equal(shown.reason, reason);
    details.add(shown.detail);
  }
  assert.equal(details.size, TRACE_LOSS_REASONS.length, "each reason reads differently");
  // `none` on a lost row is itself inconsistent; the page must not claim the content is intact.
  const none = presentContent({ content: "lost", loss_reason: "none" });
  assert.match(none.detail, /No reason was recorded/);
  assert.equal(none.failed, true);
});

test("V1-V03 the indicators count projection lag, loss and unfinished rows on this page", () => {
  const view = buildTraceListView(
    page([
      row({ request_id: "r1", content: "pending" }),
      row({ request_id: "r2", content: "pending" }),
      row({ request_id: "r3", content: "lost", loss_reason: "memory_budget" }),
      row({ request_id: "r4", content: "lost", loss_reason: "disk_error" }),
      row({ request_id: "r5", content: "lost", loss_reason: "memory_budget" }),
      row({ request_id: "r6", content: "metadata_only", trace_mode: "minimal" }),
      row({ request_id: "r7", content: "expired" }),
      row({ request_id: "r8", job_state: "running", http_status: 0, prompt_tokens: null, completion_tokens: null }),
      // The projection defect: an off row that cannot legitimately be here.
      row({ request_id: "r9", content: "off", trace_mode: "off" }),
    ]),
    { filters: FILTERS, keys: [key()] },
  );
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  assert.deepEqual(view.indicators, {
    rows: 9,
    pending: 2,
    lost: 3,
    lostReasons: [
      { reason: "memory_budget" as TraceLossReason, count: 2 },
      { reason: "disk_error" as TraceLossReason, count: 1 },
    ],
    metadataOnly: 1,
    inFlight: 1,
    unexpected: 1,
  });
  // An expired row and an off row are neither lag nor loss.
  assert.equal(view.indicators.lost, 3, "expired and off are not losses");
});

test("V1-V04 rows join key names, mark revoked keys, and never invent one", () => {
  const view = buildTraceListView(
    page([
      row({ request_id: "r1", feedback_count: 2, score_count: 7 }),
      row({ request_id: "r2", key_id: "a1000000-0000-4000-8000-000000000004" }),
      row({ request_id: "r3", key_id: "a1000000-0000-4000-8000-00000000dead" }),
    ]),
    {
      filters: FILTERS,
      keys: [
        key(),
        key({ id: "a1000000-0000-4000-8000-000000000004", name: "ci", revoked_at: "2026-09-10T00:00:00.000Z" }),
      ],
    },
  );
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  assert.deepEqual(
    view.rows.map((item) => [item.keyLabel, item.keyRevoked]),
    [
      ["production", false],
      ["ci", true],
      // No name known: the opaque id, shortened — not a guess, and not blank.
      ["a1000000…", false],
    ],
  );
  assert.equal(view.rows[0].href, "/traces/r1");
  assert.equal(view.rows[0].inFlight, false);
  // Two different signal counts, so neither column can be quietly showing the other's number.
  assert.equal(view.rows[0].feedbackCount, 2);
  assert.equal(view.rows[0].scoreCount, 7);
  assert.deepEqual(
    ["succeeded", "failed", "cancelled", "expired", "preparing", "queued", "running"].map(
      (state) => buildTraceListViewState(state),
    ),
    [false, false, false, false, true, true, true],
    "a non-terminal row is marked: its tokens and cost are not final",
  );

  function buildTraceListViewState(state: string): boolean {
    const built = buildTraceListView(page([row({ job_state: state as TraceListItem["job_state"] })]), {
      filters: FILTERS,
      keys: [key()],
    });
    return built.kind === "rows" ? built.rows[0].inFlight : false;
  }
});

test("V1-V05 status tone follows the class, and an unset status is not drawn as success", () => {
  assert.deepEqual(
    [0, 199, 200, 204, 399, 400, 404, 429, 500, 503].map(statusTone),
    ["muted", "muted", "ok", "ok", "ok", "warn", "warn", "warn", "error", "error"],
  );
});

test("V1-V06 an empty page distinguishes filtered, capture-off, keys-unknown and simply idle", () => {
  // `narrowed` comes from the parser, not from the caller: passing it by hand here would leave the
  // page free to decide a filtered list "has no traffic" whatever the URL said.
  assert.equal(PARSED_NARROWED.narrowed, true, "?state=failed is a narrowed list");
  assert.equal(PARSED_IDLE.narrowed, false, "the default window is not");

  const filtered = buildTraceListView(page([]), {
    filters: PARSED_NARROWED.filters,
    keys: [key()],
    narrowed: PARSED_NARROWED.narrowed,
  });
  assert.equal(filtered.kind, "empty");
  if (filtered.kind === "empty") {
    assert.equal(filtered.reason, "filtered");
    assert.equal(filtered.action?.label, "Clear filters");
    assert.equal(filtered.action?.href, "/traces");
  }

  // Every other narrowing the parser can report ends in the same state, from real parameters.
  for (const raw of [
    { state: "failed" },
    { content: "lost" },
    { mode: "full" },
    { feedback: "yes" },
    { range: "7d" },
    { key: "a1000000-0000-4000-8000-000000000001" },
  ]) {
    const parsed = parseTraceParams(raw, {
      now: NOW,
      keyIds: ["a1000000-0000-4000-8000-000000000001"],
    });
    const view = buildTraceListView(page([]), {
      filters: parsed.filters,
      keys: [key()],
      narrowed: parsed.narrowed,
    });
    assert.equal(view.kind === "empty" ? view.reason : "rows", "filtered", JSON.stringify(raw));
  }

  // No filters, no rows, and every active key has tracing off: the honest reason is "nothing is
  // captured", never "nothing happened" — and the requests are still visible under Usage.
  const off = buildTraceListView(page([]), {
    filters: FILTERS,
    keys: [key({ trace_mode: "off" }), key({ id: "k2", trace_mode: "full", revoked_at: "2026-09-01T00:00:00.000Z" })],
    narrowed: PARSED_IDLE.narrowed,
  });
  assert.equal(off.kind, "empty");
  if (off.kind === "empty") {
    assert.equal(off.reason, "tracing_off");
    assert.match(off.message, /Usage/);
    assert.equal(off.action?.href, "/api-keys");
  }

  // `keys.list` failed: the names are missing and so is the fact about capture, so the page must not
  // claim tracing is off for keys it could not read.
  const unknown = buildTraceListView(page([]), {
    filters: FILTERS,
    keys: [],
    narrowed: false,
    keysUnavailable: true,
  });
  assert.equal(unknown.kind, "empty");
  if (unknown.kind === "empty") {
    assert.equal(unknown.reason, "keys_unknown");
    assert.ok(!/tracing is off/i.test(unknown.message), unknown.message);
    assert.match(unknown.message, /could not be read/);
    assert.equal(unknown.action?.label, "Try again");
  }

  const idle = buildTraceListView(page([]), { filters: FILTERS, keys: [key()], narrowed: false });
  assert.equal(idle.kind, "empty");
  if (idle.kind === "empty") {
    assert.equal(idle.reason, "no_traces");
    assert.equal(idle.action, null);
  }
});

test("V1-V07 an error offers a retry only where retrying can work", () => {
  const retryable: ErrorCode[] = [
    "dependency_unavailable",
    "internal_error",
    "deadline_exceeded",
    "rate_limited",
  ];
  for (const code of retryable) {
    const view = buildTraceListView(failure(code), { filters: NARROWED, keys: [] });
    assert.equal(view.kind, "error");
    if (view.kind !== "error") continue;
    assert.equal(view.code, code);
    assert.equal(view.action?.label, "Try again");
    assert.equal(view.action?.href, traceHref(NARROWED), "a retry keeps the reader's filters");
    assert.ok(view.message.length > 0);
  }

  // A suspended organization keeps every read (R33), so this one is worth retrying and says why.
  const suspended = buildTraceListView(failure("org_suspended"), { filters: NARROWED, keys: [] });
  assert.equal(suspended.kind, "error");
  if (suspended.kind === "error") {
    assert.equal(suspended.action?.label, "Try again");
    assert.match(suspended.message, /readable/);
  }

  // A cursor the service will not accept cannot be retried — the reader is sent to the first page.
  // The link has to *leave the walk*: with the cursor still on it, the way out is the dead end.
  const stuck = parseTraceParams(
    { at: "2026-09-20T00:00:00.000Z", cursor: "Zm9yZ2Vk", state: "failed" },
    { now: NOW, keyIds: [] },
  );
  assert.equal(stuck.filters.cursor, "Zm9yZ2Vk", "the forged cursor is on the filters");
  for (const code of ["invalid_cursor", "invalid_request"] as ErrorCode[]) {
    const view = buildTraceListView(failure(code), { filters: stuck.filters, keys: [] });
    assert.equal(view.kind, "error");
    if (view.kind !== "error") continue;
    const href = view.action?.href ?? "";
    assert.notEqual(view.action?.label, "Try again", `${code} cannot be retried as it is`);
    assert.ok(!href.includes("cursor="), `${code}: the way back still carries the cursor: ${href}`);
    assert.ok(!href.includes("at="), `${code}: the way back still pins the window: ${href}`);
    assert.notEqual(href, traceHref(stuck.filters), `${code}: the way back is the same page`);
    assert.equal(href, "/traces");
  }

  for (const code of ["forbidden", "not_found"] as ErrorCode[]) {
    const view = buildTraceListView(failure(code), { filters: NARROWED, keys: [] });
    assert.equal(view.kind, "error");
    if (view.kind === "error") assert.notEqual(view.action?.label, "Try again");
  }

  // An unmapped code still produces a usable state rather than a blank page.
  const unknown = buildTraceListView(failure("journal_expired"), { filters: FILTERS, keys: [] });
  assert.equal(unknown.kind, "error");
  if (unknown.kind === "error") assert.ok(unknown.message.length > 0);
});

test("V1-V08 the next-page link carries the cursor and pins the window", () => {
  const view = buildTraceListView(page([row()], "bmV4dA=="), { filters: FILTERS, keys: [key()] });
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  assert.match(view.nextHref ?? "", /cursor=bmV4dA%3D%3D/);
  assert.match(view.nextHref ?? "", /at=/, "the walk's window is pinned into the link");

  assert.equal(view.firstHref, null, "page one is already the first page");

  const last = buildTraceListView(page([row()], null), { filters: FILTERS, keys: [key()] });
  assert.equal(last.kind === "rows" ? last.nextHref : "unset", null);

  // Deep in a walk there is one link that always works: back to the start. (The browser's back
  // button is the only way to step back one page; there is no reverse cursor in v1.)
  const deep = parseTraceParams(
    { at: "2026-09-20T00:00:00.000Z", cursor: "cGFnZTM=", state: "failed" },
    { now: NOW, keyIds: [] },
  );
  const inWalk = buildTraceListView(page([row()], "cGFnZTQ="), {
    filters: deep.filters,
    keys: [key()],
  });
  assert.equal(inWalk.kind, "rows");
  if (inWalk.kind !== "rows") return;
  assert.equal(inWalk.firstHref, "/traces?state=failed", "the filters stay, the walk is left");
  assert.match(inWalk.nextHref ?? "", /cursor=cGFnZTQ%3D/);
});

test("V1-V09 built from the fake's own page: no off row, every row explained", async () => {
  const services = createFakeConsoleServices();
  const session = services.sessions.owner;
  const keys = await services.keys.list(session);
  assert.ok(keys.ok);
  const result = await services.traces(session, { limit: 100 });
  const view = buildTraceListView(result, { filters: FILTERS, keys: keys.value, narrowed: false });
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  assert.equal(view.indicators.unexpected, 0, "a correct service returns no off row in the list");
  assert.ok(view.indicators.rows > 0);
  for (const item of view.rows) {
    assert.ok(item.content.detail.length > 0, `${item.requestId} has an unexplained content state`);
    assert.equal(item.content.failed, item.content.state === "lost");
  }
  // The fixtures contain each of the five list states, so the mapping is exercised end to end.
  assert.deepEqual(
    [...new Set(view.rows.map((item) => item.content.state))].sort(),
    ["available", "expired", "lost", "metadata_only", "pending"],
  );

  // An injected failure is the loading/error/retry path against the real interface.
  services.failNext("traces", "dependency_unavailable");
  const failed = buildTraceListView(await services.traces(session, { limit: 25 }), {
    filters: FILTERS,
    keys: keys.value,
  });
  assert.equal(failed.kind, "error");
  if (failed.kind === "error") assert.equal(failed.action?.label, "Try again");
});

test("V1-V10 the way out of a bad page link actually loads a page of rows", async () => {
  const services = createFakeConsoleServices();
  const session = services.sessions.owner;
  const keysResult = await services.keys.list(session);
  assert.ok(keysResult.ok);
  const ownKeys = keysResult.value;
  const keyIds = ownKeys.map((item) => item.id);

  /** What a reader has in their hands: a URL. Load it exactly as the page does. */
  async function open(href: string) {
    const raw = Object.fromEntries(new URL(href, "https://console.invalid").searchParams);
    const parsed = parseTraceParams(raw, { now: NOW, keyIds });
    const result = await services.traces(session, parsed.query);
    return {
      parsed,
      result,
      view: buildTraceListView(result, {
        filters: parsed.filters,
        keys: ownKeys,
        narrowed: parsed.narrowed,
      }),
    };
  }

  // A stale or forged page link: a cursor minted for some other query, with its window pinned.
  const newest = await services.traces(session, { limit: 1 });
  assert.ok(newest.ok);
  const dead = `/traces?range=30d&at=${encodeURIComponent(newest.value.items[0].created_at)}&cursor=Zm9yZ2VkLWN1cnNvcg%3D%3D`;
  const stuck = await open(dead);
  assert.equal(stuck.result.ok, false, "the service refuses a cursor it did not mint");
  if (stuck.result.ok) return;
  assert.equal(stuck.result.error.code, "invalid_cursor");
  assert.equal(stuck.view.kind, "error");
  if (stuck.view.kind !== "error") return;

  const wayBack = stuck.view.action?.href ?? "";
  // The bug this case exists for: the way back used to be the URL the reader was already on, so the
  // only escape from a bad link was editing it by hand.
  assert.notEqual(wayBack, dead, "the way back is a different page");
  assert.ok(!wayBack.includes("cursor="), wayBack);
  // No filter is set on this URL, so nothing else drops the walk: the way back has to release the
  // pinned window itself, or it hands the reader a window frozen at whenever the link was made.
  assert.ok(!wayBack.includes("at="), `the way back still pins the window: ${wayBack}`);
  assert.equal(wayBack, "/traces?range=30d", "the reader's window choice survives, the walk does not");

  // Following it has to produce rows, not the same error again.
  const recovered = await open(wayBack);
  assert.ok(recovered.result.ok, "following the way back still failed");
  assert.equal(recovered.view.kind, "rows");
  if (recovered.view.kind !== "rows") return;
  assert.ok(recovered.view.rows.length > 0, "the way back loaded an empty page");
  assert.equal(recovered.parsed.filters.cursor, null);
  assert.deepEqual(recovered.parsed.rejected, [], "and it is a clean URL");
});

test("V1-V11 the error boundary re-fetches, and shows nothing from the thrown error", () => {
  // The one invariant here that no type and no view-model case can catch: `/traces` is a Server
  // Component, so its content is a server payload that already errored. `reset()` re-renders the
  // boundary's children *without re-fetching* and replays the same failure — verified in a real
  // browser on the installed Next 16.3.5 — while `retry()` re-fetches, so it can recover once the
  // fault clears. The shipped docs say the same
  // (node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/error.md: "In most
  // cases, you should use retry() instead"; `retry` stable since v16.3.0). Next's generated route
  // types do not constrain boundary props, so this reads the source: crude, but it is the only thing
  // that fails if someone "fixes" it back.
  const file = readFileSync(new URL("../../app/(console)/traces/error.tsx", import.meta.url), "utf8");
  // The comments explain *why* it is not `reset`, so they are stripped before the code is checked.
  const source = file.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.match(file, /^"use client";/, "an error boundary is a Client Component");
  assert.match(source, /export default function \w+\(\{ retry \}/, "the boundary takes `retry`");
  assert.match(source, /onClick=\{\(\) => retry\(\)\}/, "and calls it");
  assert.ok(!/\breset\b/.test(source), "`reset` cannot recover a Server Component payload");
  // Nothing from the thrown error reaches the reader: not the message, not the digest.
  assert.ok(!/\{error\.|error\.message|error\.digest/.test(source), "no error text is rendered");
});
