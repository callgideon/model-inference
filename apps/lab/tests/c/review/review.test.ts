// C3F (FEEDBACK-ACK, LAB-ACCESS; Lab audience): a provider reviews the feedback a customer shared.
//
// `reviewFeedback` reads through the one door `public.lab_review_feedback` (WR-C3F-1) on the
// signed-in user's own session. The door checks, on the database clock, a current developer+
// membership of the selected provider AND the request's org's current grant to it naming the job's
// model, `feedback` and `provider_sharing` (L2's rule); this adapter sends only the selected workspace
// and the request id, and fails closed on any row that is not a stored customer signal. The same
// adapter runs against real PostgREST in review-postgrest.test.ts (apps/app/tests/c/feedback/stack.py).
import assert from "node:assert/strict";
import test from "node:test";
import type { Membership } from "../../../lib/auth/access.ts";
import { REVIEW_RPC, reviewFeedback, type ReviewRpc } from "../../../lib/services/review/index.ts";

const NEMO = "b0000001-0000-4000-8000-000000000001";
const JOB = "5c000000-0000-4000-8000-0000000000f1";
const workspace: Membership = { providerId: NEMO, providerName: "NemoStation", role: "developer" };
const ROW = {
  feedback_id: "fb_" + "a".repeat(64), request_id: JOB, name: "thumb", value: true, comment: null,
  author_role: "customer", channel: "console", created_at: "2026-09-27T00:00:00+00:00",
};

type Answer = { data: unknown; error: { code?: string; message?: string } | null };
function client(answer: Answer | (() => never)) {
  const calls: [string, unknown][] = [];
  const rpc: ReviewRpc = {
    rpc(name, args) {
      calls.push([name, args]);
      return Promise.resolve(typeof answer === "function" ? answer() : answer);
    },
  };
  return { rpc, calls };
}

test("C3F-L01 the review reads through the one named door with the selected workspace and the request, no identity", async () => {
  const { rpc, calls } = client({ data: [ROW], error: null });
  assert.deepEqual(await reviewFeedback(rpc, workspace, JOB), { ok: true, entries: [ROW] });
  assert.equal(REVIEW_RPC, "lab_review_feedback");
  assert.deepEqual(calls, [[REVIEW_RPC, { p_args: { provider_org_id: NEMO, request_id: JOB } }]]);
});

test("C3F-L02 a malformed request id is not_found without asking the database", async () => {
  for (const id of ["", "5c000000", `${JOB} `, 42, null, { request_id: JOB }]) {
    const { rpc, calls } = client({ data: [ROW], error: null });
    assert.deepEqual(await reviewFeedback(rpc, workspace, id), { ok: false, reason: "not_found" }, String(id));
    assert.equal(calls.length, 0);
  }
});

test("C3F-L03 the door's refusals keep their meaning; anything unexpected is unavailable, never an empty review", async () => {
  const cases: [Answer | (() => never), string][] = [
    [{ data: null, error: { code: "P0001", message: "not_found: no such request shared with this provider" } }, "not_found"],
    [{ data: null, error: { code: "P0001", message: "forbidden: this provider role does not hold manage_dev_deployment" } }, "forbidden"],
    [{ data: null, error: { code: "42501", message: "permission denied for function lab_review_feedback" } }, "forbidden"],
    [{ data: null, error: { code: "PGRST202", message: "Could not find the function" } }, "unavailable"],
    [{ data: null, error: { code: "P0001", message: "invalid_request: bad" } }, "unavailable"],
    [() => { throw new TypeError("fetch failed"); }, "unavailable"],
  ];
  for (const [answer, reason] of cases) {
    const { rpc } = client(answer);
    assert.deepEqual(await reviewFeedback(rpc, workspace, JOB), { ok: false, reason }, JSON.stringify(typeof answer === "function" ? "throw" : answer));
  }
});

test("C3F-L04 a row that is not a shared customer signal fails the whole review closed", async () => {
  const bad = [
    { ...ROW, author_principal: "c1000000-0000-4000-8000-000000000001" },   // a customer identity leaked
    { ...ROW, org_id: "0e000000-0000-4000-8000-000000000001" },
    { ...ROW, name: "calibration_label", value: "incorrect" },                // operator data (R49)
    { ...ROW, author_role: "operator" },
    { ...ROW, channel: "lab" },
    { ...ROW, request_id: "5c000000-0000-4000-8000-0000000000f2" },          // another request's row
    { ...ROW, feedback_id: 7 },
    { ...Object.fromEntries(Object.entries(ROW).filter(([k]) => k !== "comment")), author_principal: "x" }, // swapped column
    "fb",
    null,
  ];
  for (const row of bad) {
    const { rpc } = client({ data: [ROW, row], error: null });
    assert.deepEqual(await reviewFeedback(rpc, workspace, JOB), { ok: false, reason: "unavailable" }, JSON.stringify(row));
  }
  const { rpc } = client({ data: { ROW }, error: null });
  assert.deepEqual(await reviewFeedback(rpc, workspace, JOB), { ok: false, reason: "unavailable" });
  const judged = { ...ROW, author_role: "judge", channel: "api" };
  const ok = client({ data: [judged], error: null });
  assert.deepEqual(await reviewFeedback(ok.rpc, workspace, JOB), { ok: true, entries: [judged] });
});
