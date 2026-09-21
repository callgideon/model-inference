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
import test from "node:test";

import { parseTraceParams, traceHref } from "../../app/(console)/traces/query.ts";
import {
  buildTraceListView,
  presentContent,
  statusTone,
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
const FILTERS = parseTraceParams({}, { now: NOW }).filters;
const NARROWED = parseTraceParams({ state: "failed" }, { now: NOW }).filters;

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
  const expected: Record<TraceContentAvailability, { failed: boolean; unexpected: boolean }> = {
    available: { failed: false, unexpected: false },
    metadata_only: { failed: false, unexpected: false },
    pending: { failed: false, unexpected: false },
    lost: { failed: true, unexpected: false },
    expired: { failed: false, unexpected: false },
    // An off-mode request has no trace row (R13). If one ever reaches a list it is a projection
    // defect, not a capture failure, and it must not be drawn as one.
    off: { failed: false, unexpected: true },
  };
  const labels = new Set<string>();
  for (const state of TRACE_CONTENT_AVAILABILITY) {
    const shown = presentContent({ content: state, loss_reason: "memory_budget" });
    assert.equal(shown.state, state);
    assert.equal(shown.failed, expected[state].failed, `${state} failed flag`);
    assert.equal(shown.unexpected, expected[state].unexpected, `${state} unexpected flag`);
    assert.ok(shown.detail.length > 0, `${state} needs an explanation`);
    labels.add(shown.label);
    if (!expected[state].failed) {
      assert.equal(shown.reason, null, `${state} must not borrow a loss reason`);
    }
  }
  assert.equal(labels.size, TRACE_CONTENT_AVAILABILITY.length, "each state reads differently");
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
      row({ request_id: "r1" }),
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

test("V1-V06 an empty page distinguishes filtered, capture-off and simply idle", () => {
  const filtered = buildTraceListView(page([]), {
    filters: NARROWED,
    keys: [key()],
    narrowed: true,
  });
  assert.equal(filtered.kind, "empty");
  if (filtered.kind === "empty") {
    assert.equal(filtered.reason, "filtered");
    assert.equal(filtered.action?.label, "Clear filters");
    assert.equal(filtered.action?.href, "/traces");
  }

  // No filters, no rows, and every active key has tracing off: the honest reason is "nothing is
  // captured", never "nothing happened" — and the requests are still visible under Usage.
  const off = buildTraceListView(page([]), {
    filters: FILTERS,
    keys: [key({ trace_mode: "off" }), key({ id: "k2", trace_mode: "full", revoked_at: "2026-09-01T00:00:00.000Z" })],
    narrowed: false,
  });
  assert.equal(off.kind, "empty");
  if (off.kind === "empty") {
    assert.equal(off.reason, "tracing_off");
    assert.match(off.message, /Usage/);
    assert.equal(off.action?.href, "/api-keys");
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

  // A cursor the service will not accept cannot be retried — the reader is sent to the first page.
  const stale = buildTraceListView(failure("invalid_cursor"), { filters: NARROWED, keys: [] });
  assert.equal(stale.kind, "error");
  if (stale.kind === "error") {
    assert.match(stale.message, /first page/);
    assert.notEqual(stale.action?.label, "Try again");
  }

  for (const code of ["forbidden", "invalid_request", "not_found"] as ErrorCode[]) {
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

  const last = buildTraceListView(page([row()], null), { filters: FILTERS, keys: [key()] });
  assert.equal(last.kind === "rows" ? last.nextHref : "unset", null);
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
