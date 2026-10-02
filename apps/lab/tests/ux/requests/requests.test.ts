// UX-05 (03-lab.md L-05; 07-handoff UX-T09 and "Lab content"): the request list and one request,
// rendered from the real pages with synthetic records shaped like lab_traces.py's AP-07c projection
// (access_state, elapsed_ms, pins, server-side filters bound into the cursor). Every read a page makes
// is recorded by the stubs in render.ts, so "no content read" and "the session workspace only" are
// observed, not inferred.
import assert from "node:assert/strict";
import test from "node:test";
import { fixture, html, lab, text } from "./render.ts";

type Element = unknown;
type Page = (props: { searchParams?: Promise<Record<string, string | string[]>>; params?: Promise<{ id: string }> }) => Promise<Element>;
type Q = typeof import("../../../components/traces/list/query.ts");
type V = typeof import("../../../components/traces/list/view-model.ts");

const { default: Requests } = await lab<{ default: Page }>("app/(provider)/requests/page.tsx");
const { default: RequestDetail } = await lab<{ default: Page }>("app/(provider)/requests/[id]/page.tsx");
const { default: RequestsError } = await lab<{ default: () => Element }>("app/(provider)/requests/error.tsx");
const { parseListParams, listHref } = await lab<Q>("components/traces/list/query.ts");
const { buildListView, ACCESS_LABEL } = await lab<V>("components/traces/list/view-model.ts");

const ORG = "0a000000-0000-4000-8000-00000000000a";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const SECRET = '{"prompt":"<script>alert(1)</script> synthetic-secret-prompt"}';
const base = {
  request_id: REQ, started_at: "2026-09-27T10:00:00+00:00", completed_at: "2026-09-27T10:00:01.250000+00:00", mode: "full",
  loss_reason: "none", serving_version_id: "sv-1", model_revision: "acme-7b@r2", rate_card_version: "rc-3", policy_version: null,
  price_version: "pv-1", request_schema_version: 2, model_id: "acme-7b", elapsed_ms: 1250,
};
const meta = (over: Record<string, unknown> = {}) => ({ ...base, access: "metadata", access_state: "metadata", ...over });
const granted = (over: Record<string, unknown> = {}) => ({
  ...base, access: "content", access_state: "content", grantor_org_id: ORG, grant_ref: "grant-v1", content_complete: true,
  content_bytes: 812, content_available: true, ...over,
});
const page = (items: unknown[], next_cursor: string | null = null) => ({ ok: true, value: { items, next_cursor } });
const NONE = { model_id: null, serving_version_id: null };
const FEEDBACK = { ok: true, entries: [{ feedback_id: "fb_1", request_id: REQ, name: "thumb", value: true, comment: null, author_role: "customer", channel: "console", created_at: "2026-09-27T10:02:00Z" }] };
const JUDGE = { ok: true, value: [] };

async function list(searchParams: Record<string, string | string[]>, result: unknown) {
  const w = fixture({ list: result });
  return { w, markup: await html(await Requests({ searchParams: Promise.resolve(searchParams) })) };
}
async function detail(result: unknown, feedback: unknown = FEEDBACK) {
  const w = fixture({ detail: result, feedback, judge: JUDGE });
  return { w, markup: await html(await RequestDetail({ params: Promise.resolve({ id: REQ }) })) };
}
const rows = (view: ReturnType<V["buildListView"]>) => (view.kind === "rows" ? view.rows : assert.fail(`expected rows, got ${view.kind}`));

// ---------------------------------------------------------------------------------- the list

test("UX05-L01 the list reads as the session workspace with the URL's filter and cursor; a URL provider is ignored and said so", async () => {
  const { w, markup } = await list({ provider_org_id: "b0000001-0000-4000-8000-000000000001", model_id: "acme-7b", cursor: "WyJ4Il0.0f1e" }, page([meta()]));
  assert.deepEqual(w.calls, [["list", w.workspace, "WyJ4Il0.0f1e", { model_id: "acme-7b", serving_version_id: null }]]);
  assert.match(text(markup), /does not use provider_org_id/);
  assert.match(markup, /<h1[^>]*>Requests<\/h1>/);
  assert.match(text(markup), /Requests on this workspace's deployments/);
});

test("UX05-L02 a row shows named metadata: id link, readable start, server elapsed, model, revision, mode, access and loss; never an organization, size or content", async () => {
  const { markup } = await list({}, page([granted({ content: SECRET, request_id: "id/../x" }), meta({ elapsed_ms: null, completed_at: null, loss_reason: "queue_full" }), meta({ loss_reason: "spool_full" })]));
  const shown = text(markup);
  assert.match(markup, /href="\/requests\/id%2F..%2Fx"/);
  for (const piece of ["2026-09-27 10:00:00 UTC", "1250 ms", "acme-7b@r2", "full", "Shared", "Metadata only", "Unfinished", "the capture queue was full", "spool_full"]) assert.ok(shown.includes(piece), piece);
  assert.match(markup, /<time dateTime="2026-09-27T10:00:00\+00:00">/);
  assert.doesNotMatch(markup, new RegExp(`${ORG}|812|synthetic-secret|grant-v1`));
});

test("UX05-L03 every access state has its own label; an unknown state reads as unknown, never as shared", () => {
  const label = (d: unknown) => rows(buildListView(page([d]) as never, null))[0].access;
  const states = ["content", "partial", "metadata", "revoked", "expired", "not_captured"];
  const labels = states.map((s) => label(granted({ access_state: s })).label);
  assert.deepEqual(labels, ["Shared", "Partial capture", "Metadata only", "Access revoked", "Expired", "Not captured"]);
  assert.deepEqual(labels, states.map((s) => ACCESS_LABEL[s as keyof typeof ACCESS_LABEL].label));
  assert.deepEqual(label(granted({ access_state: "shared_forever" })), { label: "Unknown", tone: "warning" });
  assert.deepEqual(label(granted({ access_state: "partial" })).tone, "warning");
  assert.notEqual(label(granted()).tone, "success", "shared content is not a health verdict");
});

test("UX05-L04 page links carry the server's cursor and the active filter; only a later page offers the newest", () => {
  const filter = { model_id: "acme-7b", serving_version_id: null };
  const first = buildListView(page([meta()], "WyJ4Il0.0f1e") as never, null, filter);
  assert.ok(first.kind === "rows");
  assert.equal(first.nextHref, "/requests?model_id=acme-7b&cursor=WyJ4Il0.0f1e");
  assert.equal(first.firstHref, null);
  const later = buildListView(page([meta()]) as never, "WyJ4Il0.0f1e", filter);
  assert.ok(later.kind === "rows" && later.nextHref === null && later.firstHref === "/requests?model_id=acme-7b");
  assert.equal(listHref(null), "/requests");
  assert.equal(listHref("a+b", { model_id: "m&x=1", serving_version_id: "sv 1" }), "/requests?model_id=m%26x%3D1&serving_version_id=sv+1&cursor=a%2Bb");
  assert.equal(parseListParams({ cursor: "WyJ4Il0.0f1e" }).cursor, "WyJ4Il0.0f1e", "a filtered cursor is <position>.<digest>");
});

test("UX05-L05 the filter is the server's: a row outside it makes the page unavailable, never a filtered-looking list", () => {
  const filter = { model_id: "acme-7b", serving_version_id: null };
  const view = buildListView(page([meta(), meta({ model_id: "other-1b" })]) as never, null, filter);
  assert.equal(view.kind, "error");
  assert.ok(view.kind === "error" && view.state === "unavailable");
  const bySv = buildListView(page([meta({ serving_version_id: "sv-2" })]) as never, null, { model_id: null, serving_version_id: "sv-1" });
  assert.ok(bySv.kind === "error" && bySv.state === "unavailable");
  assert.equal(buildListView(page([meta()]) as never, null, filter).kind, "rows");
});

test("UX05-L06 empty says no requests were returned; filtered-empty says so and offers the whole list; an empty page mid-walk is a gap", async () => {
  const empty = buildListView(page([]) as never, null);
  assert.ok(empty.kind === "empty" && empty.title === "No requests returned for this workspace" && empty.clearHref === null);
  assert.match(empty.message, /capture off/);
  const filtered = buildListView(page([]) as never, null, { model_id: "acme-7b", serving_version_id: null });
  assert.ok(filtered.kind === "empty" && filtered.title === "No requests returned for this filter" && filtered.clearHref === "/requests");
  const gap = buildListView(page([], "Q1") as never, null);
  assert.ok(gap.kind === "rows" && gap.rows.length === 0 && gap.note !== null && gap.nextHref === "/requests?cursor=Q1");
  const { markup } = await list({ model_id: "acme-7b" }, page([]));
  assert.match(markup, /data-state="empty"/);
  assert.match(markup, /data-state="empty"[\s\S]*<a[^>]*href="\/requests"[^>]*>Show all requests<\/a>/);
});

test("UX05-L07 T09: an unavailable trace service keeps the heading and offers Try again on the same URL; denied and not found are their own states", async () => {
  const { markup } = await list({ model_id: "acme-7b" }, { ok: false, reason: "unavailable" });
  assert.match(markup, /<h1[^>]*>Requests<\/h1>/);
  assert.match(markup, /data-state="unavailable"[^>]*role="alert"/);
  assert.match(text(markup), /We couldn't load requests/);
  assert.match(markup, /<a[^>]*href="\/requests\?model_id=acme-7b"[^>]*>Try again<\/a>/);
  assert.doesNotMatch(text(markup), /No requests returned|<table/);
  const denied = (await list({}, { ok: false, reason: "denied" })).markup;
  assert.match(denied, /data-state="denied"/);
  assert.match(text(denied), /aggregate health only/);
  const lost = (await list({}, { ok: false, reason: "not_found" })).markup;
  assert.match(lost, /data-state="not_found"/);
  assert.doesNotMatch(denied + lost, /Try again|<table/);
});

test("UX05-L08 a refused URL value is reported and never sent: a repeated or oversized filter, a cursor that is not ours", () => {
  const p = parseListParams({ model_id: ["a", "b"], serving_version_id: "x".repeat(201), cursor: "a b" });
  assert.deepEqual(p.filter, NONE);
  assert.equal(p.cursor, null);
  assert.deepEqual(p.rejected.map((r) => r.name), ["cursor", "model_id", "serving_version_id"]);
  assert.deepEqual(parseListParams({ serving_version_id: "x".repeat(200), model_id: "" }).filter, { model_id: null, serving_version_id: "x".repeat(200) });
  assert.deepEqual(parseListParams({ cursor: "a.b.c" }).rejected.map((r) => r.name), ["cursor"]);
});

// ---------------------------------------------------------------------------------- one request

test("UX05-D01 metadata only: the content region says content is not shared, and nothing of a carried body is rendered or read", async () => {
  const { w, markup } = await detail({ ok: true, value: meta({ content: SECRET }) });
  assert.deepEqual(w.calls.map((c) => c[0]), ["detail", "feedback", "judge"], "no content read beside the record");
  assert.match(text(markup), /Content isn't shared with this workspace/);
  assert.doesNotMatch(markup, /synthetic-secret|<pre|Organization|bytes/);
  assert.match(text(markup), /acme-7b@r2/);
  const claimed = await detail({ ok: true, value: meta({ access_state: "content", content: SECRET }) });
  assert.match(text(claimed.markup), /could not be determined/, "a metadata record claiming content shows none");
  assert.doesNotMatch(claimed.markup, /synthetic-secret|<pre/);
});

test("UX05-D02 expired and revoked content disappears: each state's copy, the allowed metadata kept, no body", async () => {
  for (const [record, copy] of [
    [granted({ access_state: "expired", content: SECRET, content_available: false }), /past its retention or was deleted/],
    [meta({ access_state: "revoked", content: SECRET }), /revoked or has lapsed/],
    [granted({ access_state: "revoked", content: SECRET }), /revoked or has lapsed/],
    [granted({ access_state: "not_captured", loss_reason: "disk_error", content: SECRET }), /not captured.*the spool could not be written/],
  ] as const) {
    const { markup } = await detail({ ok: true, value: record });
    assert.match(text(markup), copy);
    assert.doesNotMatch(markup, /synthetic-secret|<pre/);
    assert.match(text(markup), new RegExp(REQ));
  }
});

test("UX05-D03 partial capture is marked and its body is not presented as complete", async () => {
  const { markup } = await detail({ ok: true, value: granted({ access_state: "partial", content_complete: false, content: "half an answer" }) });
  assert.match(text(markup), /Partial capture/);
  assert.match(text(markup), /incomplete/);
  assert.match(markup, /<pre[^>]*>half an answer<\/pre>/);
});

test("UX05-D04 shared content is text with its provenance (organization, grant, size); markup inside it is never executed", async () => {
  const { markup } = await detail({ ok: true, value: granted({ content: SECRET }) });
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt; synthetic-secret-prompt/);
  assert.doesNotMatch(markup, /<script/);
  for (const piece of [ORG, "grant-v1", "812 bytes"]) assert.ok(text(markup).includes(piece), piece);
  const unread = await detail({ ok: true, value: granted({ content: null }) });
  assert.match(text(unread.markup), /could not be read/);
  assert.doesNotMatch(unread.markup, /<pre/);
});

test("UX05-D05 a request outside the session workspace is not found: the unprojected copy, no metadata, read as the session workspace", async () => {
  const { w, markup } = await detail({ ok: false, reason: "not_found" });
  assert.deepEqual(w.calls[0], ["detail", w.workspace, REQ]);
  assert.match(markup, /<h1[^>]*>Request<\/h1>/);
  assert.match(markup, /data-state="not_found"/);
  assert.match(text(markup), /not be projected yet/);
  assert.doesNotMatch(markup, /Model revision|another provider|Judge/);
  assert.match(markup, /data-state="not_found"[\s\S]*href="\/requests"/);
});

test("UX05-D06 T09: an unavailable trace read keeps the heading and offers Try again, and feedback still shows", async () => {
  const { markup } = await detail({ ok: false, reason: "unavailable" });
  assert.match(markup, /<h1[^>]*>Request<\/h1>/);
  assert.match(markup, /data-state="unavailable"[^>]*role="alert"/);
  assert.match(markup, new RegExp(`<a[^>]*href="/requests/${REQ}"[^>]*>Try again</a>`));
  assert.match(text(markup), /thumbs up/);
  const refused = await detail({ ok: false, reason: "unavailable" }, { ok: false, reason: "unavailable" });
  assert.match(text(refused.markup), /Feedback could not be read/);
});

test("UX05-D07 the summary names request, model, revision and serving identity with copy controls, the pins, and links to that serving version's requests", async () => {
  const { markup } = await detail({ ok: true, value: granted() });
  const shown = text(markup);
  for (const piece of ["1250 ms", "pv-1", "rc-3", "acme-7b@r2", "sv-1", "2026-09-27 10:00:01 UTC"]) assert.ok(shown.includes(piece), piece);
  assert.match(markup, /aria-label="Copy request id"/);
  assert.match(markup, /aria-label="Copy serving version id"/);
  assert.match(markup, /href="\/requests\?serving_version_id=sv-1"/);
  assert.match(markup, /href="\/requests\?model_id=acme-7b"/);
  assert.match(markup, /<nav aria-label="Breadcrumb">[\s\S]*href="\/requests"/);
  const open = text((await detail({ ok: true, value: meta({ completed_at: null, elapsed_ms: null, rate_card_version: null }) })).markup);
  assert.ok(open.includes("Unfinished") && open.includes("unpriced"));
});

test("UX05-E01 the error boundary keeps the heading and offers the newest requests", async () => {
  const markup = await html(RequestsError());
  assert.match(markup, /<h1[^>]*>Requests<\/h1>/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /<a[^>]*href="\/requests"[^>]*>Start from the newest requests<\/a>/);
});
