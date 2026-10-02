// V2 (CONSOLE-FLOWS, FEEDBACK-ACK, TRACE-TENANT): what a provider sees of one request's feedback and
// refusals, from C3F's review door and the provider trace read. The metadata and content-access cases
// (V2-D01..D03) moved to tests/ux/requests (UX05-D*) with AP-07c's access_state.
import assert from "node:assert/strict";
import test from "node:test";
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import { tracePorts, type Actor } from "../../../components/traces/detail/port.ts";
import { feedbackView, FEEDBACK_COPY, TRACE_COPY } from "../../../components/traces/detail/view.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const actor: Actor = { providerId: A, role: "developer" };

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
  assert.equal(feedbackView({ ok: true, entries: [], reviews: [] }).empty, FEEDBACK_COPY.empty);
  for (const reason of ["not_found", "forbidden", "unavailable"] as const)
    assert.deepEqual(feedbackView({ ok: false, reason }), { rows: [], note: FEEDBACK_COPY.note, empty: FEEDBACK_COPY[reason] });
});

test("V2-D06 a missing request reads the same whether it is another provider's or not projected yet", () => {
  assert.match(TRACE_COPY.not_found, /not be projected yet/);
  assert.doesNotMatch(TRACE_COPY.not_found, /another|provider's|forbidden/i);
  assert.ok(TRACE_COPY.denied && TRACE_COPY.unavailable);
});

test("V2-D07 the trace port fails closed whatever the environment says, and there is no content port", async () => {
  process.env.LAB_TRACES_PREVIEW = "1";
  const ports = tracePorts();
  assert.deepEqual(Object.keys(ports), ["traces"]);
  assert.deepEqual(await ports.traces.detail(actor, REQ), { ok: false, reason: "unavailable" });
});
