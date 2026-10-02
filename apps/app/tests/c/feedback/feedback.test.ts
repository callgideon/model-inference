// node --test "tests/**/*.test.ts"
//
// C3F (FEEDBACK-ACK, App audience) over infrx-api (AP-09 09b): the consumer's own-feedback action is
// one `POST /console/v1/requests/{id}/feedback` with the submission's Idempotency-Key. The API
// derives the org, author, channel and role and acknowledges only what it stored; this adapter
// forwards the allowlisted signal and reads back the acknowledgment. Each case names the defect it
// catches.

import assert from "node:assert/strict";
import test from "node:test";
import { answer, envelope, recordingApi } from "../../../lib/fake-api.ts";
import { submitFeedback } from "../../../lib/services/actions.ts";

const JOB = "5c000000-0000-4000-8000-0000000000f1";
const input = { request_id: JOB, name: "rating", value: 4, comment: "fine", idempotency_key: "fb-1" };
const ACK = { feedback_id: "fb_" + "a".repeat(64), request_id: JOB, author_role: "customer", channel: "console", created_at: "2026-09-27T00:00:00+00:00", replayed: false };
const codeOf = (r: { ok: boolean; error?: { code: string } }) => (r.ok ? "ok" : r.error!.code);

test("C3F-A01 the action forwards only the signal, as one feedback POST on the request, with the submission's key", async () => {
  const { api, sent } = recordingApi(() => answer(201, ACK));
  const ack = await submitFeedback(api, input);
  assert.deepEqual(ack, { ok: true, value: { id: ACK.feedback_id, request_id: JOB, created_at: "2026-09-27T00:00:00.000000Z", replayed: false } });
  assert.deepEqual(sent.map((s) => [s.method, s.path, s.body, s.idempotencyKey]), [
    ["POST", `/console/v1/requests/${JOB}/feedback`, { name: "rating", value: 4, comment: "fine" }, "fb-1"],
  ]);
});

test("C3F-A02 a smuggled provenance field is invalid_request and never reaches the API", async () => {
  const { api, sent } = recordingApi(() => answer(201, ACK));
  for (const field of ["author_role", "channel", "org_id", "calibration_set", "rubric_version", "author_principal"]) {
    assert.equal(codeOf(await submitFeedback(api, { ...input, [field]: "x" })), "invalid_request", field);
  }
  assert.deepEqual(sent, []);
});

test("C3F-A03 a malformed request id or a missing idempotency key never reaches the API", async () => {
  const { api, sent } = recordingApi(() => answer(201, ACK));
  assert.equal(codeOf(await submitFeedback(api, { ...input, request_id: "../keys" })), "not_found");
  assert.equal(codeOf(await submitFeedback(api, { ...input, idempotency_key: "" })), "invalid_request");
  assert.equal(codeOf(await submitFeedback(api, { ...input, idempotency_key: undefined })), "invalid_request");
  assert.equal(codeOf(await submitFeedback(api, { ...input, value: { nested: true } })), "invalid_request");
  assert.deepEqual(sent, []);
});

test("C3F-A04 the API's refusals keep their code; its text never reaches the caller", async () => {
  for (const [status, code] of [[404, "not_found"], [409, "idempotency_conflict"], [403, "org_suspended"], [422, "invalid_request"], [403, "forbidden"]] as const) {
    const result = await submitFeedback(recordingApi(() => answer(status, envelope(code))).api, input);
    assert.equal(codeOf(result), code);
    assert.ok(!result.ok && !result.error.message.includes("SECRET"), code);
  }
});

test("C3F-A05 the feature off, a dead session and a lost answer are never a success", async () => {
  assert.equal(codeOf(await submitFeedback(recordingApi(() => answer(503, envelope("dependency_unavailable"))).api, input)), "dependency_unavailable");
  assert.equal(codeOf(await submitFeedback(recordingApi(() => answer(500, envelope("something_new"))).api, input)), "dependency_unavailable", "an unknown code is not passed through");
  assert.equal(codeOf(await submitFeedback(recordingApi(() => answer(401, envelope("invalid_api_key"))).api, input)), "forbidden");
  assert.equal(codeOf(await submitFeedback(recordingApi(() => { throw new Error("down"); }).api, input)), "dependency_unavailable");
});

test("C3F-A06 an acknowledgment is only this request's console signal; anything else fails closed", async () => {
  for (const ack of [{ ...ACK, channel: "api" }, { ...ACK, request_id: "5c000000-0000-4000-8000-0000000000f2" }]) {
    assert.equal(codeOf(await submitFeedback(recordingApi(() => answer(201, ack)).api, input)), "internal_error", JSON.stringify(ack));
  }
  assert.equal(codeOf(await submitFeedback(recordingApi(() => answer(201, { ...ACK, created_at: "yesterday" })).api, input)), "internal_error", "an unreadable acknowledgment is unconfirmed");
  const replayed = await submitFeedback(recordingApi(() => answer(201, { ...ACK, replayed: true })).api, input);
  assert.ok(replayed.ok && replayed.value.replayed === true, "a replay is the same acknowledgment");
});
