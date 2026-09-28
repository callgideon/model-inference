// V1M (CONSOLE-TENANT, TRACE-TENANT): the request list, derived only from the provider trace read. Each
// row opens V2's detail page (WR-V2-5); content is labelled honestly and never shown in the list.
import assert from "node:assert/strict";
import test from "node:test";
import type { TraceDetail } from "../../../components/traces/detail/port.ts";
import { TRACE_COPY } from "../../../components/traces/detail/view.ts";
import { buildListView, LIST_COPY, type ListView } from "../../../components/traces/list/view-model.ts";

const ORG = "0a000000-0000-4000-8000-00000000000a";
const base = {
  request_id: "5c000000-0000-4000-8000-0000000000f1", started_at: "2026-09-27T10:00:00.000Z", completed_at: "2026-09-27T10:00:01.250Z", mode: "full",
  loss_reason: "none", serving_version_id: "sv-1", model_revision: "acme-7b@r2", rate_card_version: "rc-3", policy_version: null, model_id: "acme-7b",
} as const;
const meta = (over: Record<string, unknown> = {}): TraceDetail => ({ ...base, ...over, access: "metadata" }) as TraceDetail;
const granted = (available: boolean): TraceDetail => ({
  ...base, access: "content", grantor_org_id: ORG, grant_ref: "", content_complete: true, content_bytes: 812, content_available: available,
});
const page = (items: TraceDetail[], next_cursor: string | null = null) => ({ ok: true as const, value: { items, next_cursor } });
const rows = (view: ListView) => (view.kind === "rows" ? view.rows : assert.fail(`expected rows, got ${view.kind}`));

test("V1M-L01 each row links to its request page and shows named metadata only: no organization, size or content", () => {
  const [row] = rows(buildListView(page([meta({ request_id: "id/../x" })]), null));
  assert.equal(row.href, "/requests/id%2F..%2Fx");
  assert.deepEqual([row.started, row.duration, row.model], ["2026-09-27T10:00:00.000Z", "1250 ms", "acme-7b@r2"]);
  const [shared] = rows(buildListView(page([granted(true)]), null));
  assert.doesNotMatch(JSON.stringify(shared), new RegExp(`${ORG}|812`));
  const [open] = rows(buildListView(page([meta({ completed_at: null })]), null));
  assert.equal(open.duration, "in progress");
});

test("V1M-L02 content reads honestly: shared, metadata only, not captured, lost with its reason, expired", () => {
  const label = (d: TraceDetail) => rows(buildListView(page([d]), null))[0].content;
  assert.equal(label(granted(true)), "Shared");
  assert.equal(label(meta()), "Metadata only");
  assert.equal(label(meta({ mode: "minimal" })), "Not captured");
  assert.equal(label(meta({ loss_reason: "queue_full" })), "Lost: the capture queue was full");
  assert.equal(label(meta({ loss_reason: "spool_full" })), "Lost: spool_full");
  assert.equal(label(granted(false)), "Expired");
});

test("V1M-L03 an empty workspace says capture-off requests leave no record; an empty page with more to come says so", () => {
  assert.deepEqual(buildListView(page([]), null), { kind: "empty", message: LIST_COPY.empty });
  assert.match(LIST_COPY.empty, /capture off/);
  assert.deepEqual(buildListView(page([], "Q1"), null), { kind: "rows", rows: [], note: LIST_COPY.gap, nextHref: "/requests?cursor=Q1", firstHref: null });
  assert.deepEqual(buildListView(page([]), "Q0"), { kind: "rows", rows: [], note: LIST_COPY.gap, nextHref: null, firstHref: "/requests" });
  const gap = buildListView(page([], "Q1"), "Q0");
  assert.deepEqual(gap, { kind: "rows", rows: [], note: LIST_COPY.gap, nextHref: "/requests?cursor=Q1", firstHref: "/requests" });
});

test("V1M-L04 the next link is the server's cursor, and the way back to the first page shows only on a later page", () => {
  const first = buildListView(page([meta()], "Q1"), null);
  assert.ok(first.kind === "rows" && first.nextHref === "/requests?cursor=Q1" && first.firstHref === null && first.note === null);
  const last = buildListView(page([meta()]), "Q1");
  assert.ok(last.kind === "rows" && last.nextHref === null && last.firstHref === "/requests");
});

test("V1M-L05 a refusal is fixed copy: a viewer's role, a lost workspace, an unreachable service; never rows", () => {
  const refused = (reason: "denied" | "not_found" | "unavailable", cursor: string | null = null) => buildListView({ ok: false, reason }, cursor);
  assert.deepEqual(refused("denied"), { kind: "error", message: TRACE_COPY.denied, firstHref: null });
  assert.deepEqual(refused("unavailable"), { kind: "error", message: TRACE_COPY.unavailable, firstHref: null });
  assert.deepEqual(refused("not_found"), { kind: "error", message: LIST_COPY.not_found, firstHref: null });
  assert.deepEqual(refused("unavailable", "Q1"), { kind: "error", message: TRACE_COPY.unavailable, firstHref: "/requests" });
});
