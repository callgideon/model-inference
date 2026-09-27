// V2 (CONSOLE-FLOWS, FEEDBACK-ACK, TRACE-TENANT): what a provider sees of one request, derived only
// from the provider trace read (WR-V1M-2 / WR-V2-1), C2 content refs (WR-V2-2) and C3F's review door.
import assert from "node:assert/strict";
import test from "node:test";
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import { tracePorts, type Actor, type ContentPort, type TraceDetail } from "../../../components/traces/detail/port.ts";
import { contentState, contentView, CONTENT_COPY, feedbackView, FEEDBACK_COPY, metadataRows, TRACE_COPY } from "../../../components/traces/detail/view.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const ORG = "0a000000-0000-4000-8000-00000000000a";
const actor: Actor = { providerId: A, role: "developer" };
const base = {
  request_id: REQ, started_at: "2026-09-27T10:00:00.000Z", completed_at: "2026-09-27T10:00:01.250Z", mode: "full", loss_reason: "none",
  serving_version_id: "sv-1", model_revision: "acme-7b@r2", rate_card_version: "rc-3", policy_version: "pol-1", model_id: "acme-7b",
} as const;
const meta: TraceDetail = { ...base, access: "metadata" };
const granted: TraceDetail = {
  ...base, access: "content", grantor_org_id: ORG, grant_ref: "grant-1", content_complete: true, content_bytes: 812, content_available: true,
};

function content(answer: Awaited<ReturnType<ContentPort["read"]>>) {
  const calls: unknown[][] = [];
  const port: ContentPort = { read: async (...args) => (calls.push(args), answer) };
  return { port, calls };
}

test("V2-D01 metadata rows are named fields only; without a grant no organization, size or content is shown", () => {
  const rows = Object.fromEntries(metadataRows(granted));
  assert.equal(rows.Duration, "1250 ms");
  assert.equal(rows["Rate card"], "rc-3");
  assert.equal(rows.Organization, ORG);
  assert.equal(rows["Content size"], "812 bytes");
  const leaky = { ...meta, grantor_org_id: ORG, content: "secret prompt", key_id: "k-customer" } as TraceDetail;
  const shown = JSON.stringify(metadataRows(leaky));
  assert.doesNotMatch(shown, new RegExp(`${ORG}|secret|k-customer|bytes`));
  const open = Object.fromEntries(metadataRows({ ...meta, completed_at: null, rate_card_version: null, loss_reason: "queue_full" }));
  assert.deepEqual([open.Completed, open.Duration, open["Rate card"], open.Loss], ["in progress", "—", "unpriced", "queue_full"]);
});

test("V2-D02 content states are honest: not captured, lost, metadata only, expired or tombstoned, else offered", () => {
  assert.equal(contentState({ ...granted, mode: "off" }), "not_captured");
  assert.equal(contentState({ ...granted, mode: "minimal" }), "not_captured");
  assert.equal(contentState({ ...granted, loss_reason: "disk_error" }), "lost");
  assert.equal(contentState(meta), "metadata_only");
  assert.equal(contentState({ ...granted, content_available: false }), "expired");
  assert.equal(contentState(granted), "available");
  for (const state of Object.keys(CONTENT_COPY)) assert.ok(CONTENT_COPY[state as keyof typeof CONTENT_COPY].length > 0, state);
});

test("V2-D03 content is read only on demand, through C2, as the session's provider with the row's grant", async () => {
  const idle = content({ ok: true, value: { text: "hi", expiresAt: "2026-09-27T10:05:00Z" } });
  const offered = await contentView(idle.port, actor, granted, false);
  assert.deepEqual([offered.state, offered.offer, offered.text, idle.calls.length], ["available", true, null, 0]);
  for (const d of [meta, { ...granted, content_available: false }]) {
    const view = await contentView(idle.port, actor, d, true);
    assert.deepEqual([view.offer, view.text, idle.calls.length], [false, null, 0]);
  }
  const read = await contentView(idle.port, actor, granted, true);
  assert.deepEqual(idle.calls, [[actor, "grant-1", REQ]]);
  assert.deepEqual([read.state, read.text, read.expiresAt, read.offer], ["available", "hi", "2026-09-27T10:05:00Z", false]);
  const partial = await contentView(idle.port, actor, { ...granted, content_complete: false }, true);
  assert.equal(partial.copy, CONTENT_COPY.partial);
});

test("V2-D04 a refused ref is revoked, expired or unavailable, never text", async () => {
  const cases = [["forbidden", "revoked"], ["not_found", "revoked"], ["expired", "expired"], ["unavailable", "unavailable"]] as const;
  for (const [reason, state] of cases) {
    const view = await contentView(content({ ok: false, reason }).port, actor, granted, true);
    assert.deepEqual([view.state, view.text, view.copy], [state, null, CONTENT_COPY[state]], reason);
  }
});

test("V2-D05 feedback is labelled with its stored provenance and is never presented as a calibration label", () => {
  const entry = (over: object) => ({
    feedback_id: "fb_1", request_id: REQ, name: "thumb", value: true, comment: null, author_role: "customer", channel: "console",
    created_at: "2026-09-27T10:02:00Z", ...over,
  });
  const ok: ReviewResult = {
    ok: true,
    entries: [entry({}), entry({ feedback_id: "fb_2", name: "rating", value: 4, channel: "api" }), entry({ feedback_id: "fb_3", name: "correction", value: "use 3", author_role: "judge", comment: "why" })],
  } as ReviewResult;
  const view = feedbackView(ok);
  assert.deepEqual(view.rows.map((r) => [r.what, r.who]), [["thumbs up", "customer · console"], ["rating 4", "customer · api"], ["correction: use 3", "judge · console"]]);
  assert.deepEqual(view.rows.map((r) => r.text), [null, null, "why"]);
  assert.equal(feedbackView({ ok: true, entries: [entry({ value: false })] } as ReviewResult).rows[0].what, "thumbs down");
  assert.equal(view.note, FEEDBACK_COPY.note);
  assert.match(FEEDBACK_COPY.note, /never a calibration label/);
  assert.equal(feedbackView({ ok: true, entries: [] }).empty, FEEDBACK_COPY.empty);
  for (const reason of ["not_found", "forbidden", "unavailable"] as const)
    assert.deepEqual(feedbackView({ ok: false, reason }), { rows: [], note: FEEDBACK_COPY.note, empty: FEEDBACK_COPY[reason] });
});

test("V2-D06 a missing request reads the same whether it is another provider's or not projected yet", () => {
  assert.match(TRACE_COPY.not_found, /not be projected yet/);
  assert.doesNotMatch(TRACE_COPY.not_found, /another|provider's|forbidden/i);
  assert.ok(TRACE_COPY.denied && TRACE_COPY.unavailable);
});

test("V2-D07 the trace ports fail closed: unavailable until the real adapters are wired, whatever the environment says", async () => {
  process.env.LAB_TRACES_PREVIEW = "1";
  const { traces, content: c } = tracePorts();
  assert.deepEqual(await traces.detail(actor, REQ), { ok: false, reason: "unavailable" });
  assert.deepEqual(await c.read(actor, "grant-1", REQ), { ok: false, reason: "unavailable" });
});
