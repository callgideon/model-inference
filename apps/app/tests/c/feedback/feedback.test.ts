// node --test "tests/**/*.test.ts"
//
// C3F (FEEDBACK-ACK, App audience): the consumer's own-feedback action, `submitOwnFeedback`.
//
// Each case names the defect it catches. The door (`public.submit_feedback`, WR-C3F-1) derives the
// org, author, channel, operator marker and idempotency digest itself; this adapter only forwards the
// allowlisted signal from a ready session and reads back what the database stored. The same adapter
// runs against real PostgREST in `feedback-postgrest.test.ts` (stack.py).

import assert from "node:assert/strict";
import test from "node:test";
import type { ConsumerContext } from "../../../lib/contracts/v2/consumer.ts";
import { SUBMIT_FEEDBACK_RPC, submitOwnFeedback, type FeedbackRpc } from "../../../lib/services/feedback.ts";

const ME = "c1000000-0000-4000-8000-000000000001";
const JOB = "5c000000-0000-4000-8000-0000000000f1";
const ready: ConsumerContext = {
  state: "ready",
  account: { userId: ME, email: "me@example.com", walletId: "aaaaaaaa-0000-4000-8000-000000000001", orgId: "0e000000-0000-4000-8000-000000000001", suspended: false },
};
const input = { request_id: JOB, name: "rating", value: 4, comment: "fine", idempotency_key: "fb-1" };
const STORED = {
  feedback_id: "fb_" + "a".repeat(64), request_id: JOB, org_id: "0e000000-0000-4000-8000-000000000001",
  author_principal: ME, author_role: "customer", channel: "console", name: "rating", value: 4, comment: "fine",
  calibration_set: false, rubric_version: null, by_operator: false, created_at: "2026-09-27T00:00:00+00:00",
};

type Answer = { data: unknown; error: { code?: string; message?: string } | null };
function client(answer: Answer | (() => never)) {
  const calls: [string, unknown][] = [];
  const rpc: FeedbackRpc = {
    rpc(name, args) {
      calls.push([name, args]);
      return Promise.resolve(typeof answer === "function" ? answer() : answer);
    },
  };
  return { rpc, calls };
}

test("C3F-A01 the action forwards only the signal, over the one named door, from a ready session", async () => {
  const { rpc, calls } = client({ data: STORED, error: null });
  const result = await submitOwnFeedback(ready, rpc, input);
  assert.deepEqual(calls, [[SUBMIT_FEEDBACK_RPC, { p_args: input }]]);
  assert.equal(SUBMIT_FEEDBACK_RPC, "submit_feedback");
  assert.deepEqual(result, {
    ok: true,
    value: {
      id: STORED.feedback_id, request_id: JOB, created_at: STORED.created_at, channel: "console", author_role: "customer",
      author_principal: ME, name: "rating", value: 4, comment: "fine", calibration_set: false, rubric_version: null,
    },
  });
});

test("C3F-A02 a smuggled provenance field is invalid_request and never reaches the door", async () => {
  for (const field of ["author_role", "author_principal", "channel", "org_id", "calibration_set", "by_operator", "rubric_version"]) {
    const { rpc, calls } = client({ data: STORED, error: null });
    const result = await submitOwnFeedback(ready, rpc, { ...input, [field]: "operator" });
    assert.equal(result.ok ? "ok" : result.error.code, "invalid_request", field);
    assert.equal(calls.length, 0, field);
  }
  const { rpc, calls } = client({ data: STORED, error: null });
  assert.equal((await submitOwnFeedback(ready, rpc, "rating")).ok, false);
  assert.equal(calls.length, 0);
});

test("C3F-A03 only a ready session submits; nothing else reaches the door", async () => {
  const cases: [ConsumerContext, string][] = [
    [{ state: "signed_out" }, "forbidden"],
    [{ state: "unverified", userId: ME, email: "me@example.com" }, "forbidden"],
    [{ state: "onboarding", userId: ME, email: "me@example.com" }, "forbidden"],
    [{ state: "unavailable" }, "dependency_unavailable"],
  ];
  for (const [context, code] of cases) {
    const { rpc, calls } = client({ data: STORED, error: null });
    const result = await submitOwnFeedback(context, rpc, input);
    assert.equal(result.ok ? "ok" : result.error.code, code, context.state);
    assert.equal(calls.length, 0, context.state);
  }
});

test("C3F-A04 the door's refusals keep their code; its text never reaches the caller", async () => {
  const secret = "no request 5c000000 owned by org 0e000000 (relation infrx.jobs)";
  for (const code of ["not_found", "invalid_request", "idempotency_conflict", "org_suspended"]) {
    const { rpc } = client({ data: null, error: { code: "P0001", message: `${code}: ${secret}` } });
    const result = await submitOwnFeedback(ready, rpc, input);
    assert.ok(!result.ok, `${code}: expected a refusal`);
    assert.equal(result.error.code, code);
    assert.ok(!result.error.message.includes("infrx") && !result.error.message.includes("0e000000"), result.error.message);
  }
});

test("C3F-A05 flag off, a missing door, a denied role and a lost answer are never a success", async () => {
  const cases: [Answer | (() => never), string][] = [
    [{ data: null, error: { code: "0A000", message: "feedback is not enabled" } }, "dependency_unavailable"],
    [{ data: null, error: { code: "PGRST202", message: "Could not find the function public.submit_feedback" } }, "dependency_unavailable"],
    [{ data: null, error: { code: "42501", message: "permission denied for function submit_feedback" } }, "forbidden"],
    [{ data: null, error: { code: "P0001", message: "state_conflict: something new" } }, "dependency_unavailable"],
    [() => { throw new TypeError("fetch failed"); }, "dependency_unavailable"],
  ];
  for (const [answer, code] of cases) {
    const { rpc } = client(answer);
    const result = await submitOwnFeedback(ready, rpc, input);
    assert.equal(result.ok ? "ok" : result.error.code, code, JSON.stringify(typeof answer === "function" ? "throw" : answer.error));
  }
});

test("C3F-A06 an acknowledgment is only a stored customer console signal; any other provenance fails closed", async () => {
  for (const [field, value] of [
    ["author_role", "operator"], ["channel", "api"], ["calibration_set", true], ["name", "calibration_label"], ["rubric_version", 2],
  ] as const) {
    const { rpc } = client({ data: { ...STORED, [field]: value }, error: null });
    const result = await submitOwnFeedback(ready, rpc, input);
    assert.equal(result.ok ? "ok" : result.error.code, "internal_error", field);
  }
  for (const data of [null, [], { ...STORED, feedback_id: undefined }]) {
    const { rpc } = client({ data, error: null });
    assert.equal((await submitOwnFeedback(ready, rpc, input)).ok, false, JSON.stringify(data));
  }
});
