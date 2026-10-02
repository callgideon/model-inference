// C3F (FEEDBACK-ACK, LAB-ACCESS; Lab audience), AP-09 09c: a provider reviews the feedback a customer
// shared, and records its own human review, over AP-08's `/lab/v1/traces/{id}/feedback|reviews`
// (replaces the direct lab_review_feedback RPC). The route runs the same door as the signed-in user
// (a current developer+ membership AND the request's org's current grant naming the model, `feedback`
// and `provider_sharing`); this adapter sends only the selected workspace and the request id, and
// fails closed on any signal that is not a stored customer (or judge) signal or any review that is
// not a human one. The fake API stands for the routes and records what was sent.
import assert from "node:assert/strict";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import type { Membership } from "../../../lib/auth/access.ts";
import { reviewFeedback, submitReview } from "../../../lib/services/review/index.ts";

const NEMO = "b0000001-0000-4000-8000-000000000001";
const JOB = "5c000000-0000-4000-8000-0000000000f1";
const KEY = "a1000000-0000-4000-8000-0000000000a1";
const RUN = "e0000000-0000-4000-8000-00000000000e";
const workspace: Membership = { providerId: NEMO, providerName: "NemoStation", role: "developer", capabilities: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"] };
const viewer: Membership = { ...workspace, role: "viewer", capabilities: ["read_aggregate_health"] };
const SIGNAL = {
  feedback_id: "fb_" + "a".repeat(64), name: "thumb", value: true, comment: null,
  author_role: "customer", channel: "console", created_at: "2026-09-27T00:00:00+00:00",
};
const REVIEW = {
  review_id: "f1000000-0000-4000-8000-0000000000f1", request_id: JOB, reviewer: "u1000000-0000-4000-8000-0000000000u1", provenance: "human",
  verdict: "fail", comment: "missed step 3", run_id: null, rubric_version: null, created_at: "2026-09-28T00:00:00+00:00",
};
const ERR = (code: string) => ({ error: { code, message: "m", request_id: "q", retryable: false } });
const entry = (s: typeof SIGNAL, request = JOB) => ({ ...s, request_id: request });

type Sent = { method: string; url: string; key: string | null; auth: string | null; body: unknown };
function api(status: number, body: unknown, throws = false) {
  const sent: Sent[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const h = new Headers(init.headers);
    sent.push({ method: String(init.method), url: String(url), key: h.get("idempotency-key"), auth: h.get("authorization"), body: init.body ? JSON.parse(String(init.body)) : null });
    if (throws) throw new TypeError("fetch failed");
    return new Response(JSON.stringify(body), { status });
  }) as typeof globalThis.fetch;
  return { api: labApi({ baseUrl: "https://lab-control.example", fetch, session: () => ({ token: "tok" }) }), sent };
}

test("C3F-L01 the review reads the request's feedback route with the selected workspace and the request, no identity", async () => {
  const w = api(200, { signals: [SIGNAL], reviews: [REVIEW] });
  assert.deepEqual(await reviewFeedback(w.api, workspace, JOB), { ok: true, entries: [entry(SIGNAL)], reviews: [REVIEW] });
  assert.deepEqual(w.sent, [{ method: "GET", url: `https://lab-control.example/lab/v1/traces/${JOB}/feedback?provider_org_id=${NEMO}`, key: null, auth: "Bearer tok", body: null }]);
});

test("C3F-L02 a malformed request id is not_found without asking the API; a misconfigured Lab is unavailable", async () => {
  for (const id of ["", "5c000000", `${JOB} `, 42, null, { request_id: JOB }]) {
    const w = api(200, { signals: [SIGNAL], reviews: [] });
    assert.deepEqual(await reviewFeedback(w.api, workspace, id), { ok: false, reason: "not_found" }, String(id));
    assert.equal(w.sent.length, 0);
  }
  assert.deepEqual(await reviewFeedback(null, workspace, JOB), { ok: false, reason: "unavailable" });
});

test("C3F-L05 an uppercase or non-v4 request id reaches the route (shapes UUID_ANY_RE, LAB-10)", async () => {
  for (const id of [JOB.toUpperCase(), "6ba7b810-9dad-11d1-80b4-00c04fd430c8"]) {
    const w = api(200, { signals: [], reviews: [] });
    assert.deepEqual(await reviewFeedback(w.api, workspace, id), { ok: true, entries: [], reviews: [] }, id);
    assert.equal(w.sent.length, 1);
  }
});

test("C3F-L03 the route's refusals keep their meaning; anything unexpected is unavailable, never an empty review", async () => {
  const cases: [number, unknown, boolean, string][] = [
    [404, ERR("not_found"), false, "not_found"],
    [403, ERR("forbidden"), false, "forbidden"],
    [403, { refusal: "denied" }, false, "forbidden"],
    [401, ERR("unauthenticated"), false, "unavailable"],
    [503, ERR("unavailable"), false, "unavailable"],
    [422, ERR("invalid_request"), false, "unavailable"],
    [200, null, true, "unavailable"],
  ];
  for (const [status, body, throws, reason] of cases) {
    assert.deepEqual(await reviewFeedback(api(status, body, throws).api, workspace, JOB), { ok: false, reason }, `${status} ${JSON.stringify(body)}`);
  }
});

test("C3F-L04 a signal that is not a shared customer signal, or a review that is not a human one, fails the whole review closed", async () => {
  const bad = [
    { signals: [SIGNAL, { ...SIGNAL, author_principal: "c1000000-0000-4000-8000-000000000001" }], reviews: [] }, // a customer identity leaked
    { signals: [SIGNAL, { ...SIGNAL, org_id: "0e000000-0000-4000-8000-000000000001" }], reviews: [] },
    { signals: [{ ...SIGNAL, name: "calibration_label", value: "incorrect" }], reviews: [] },               // operator data (R49)
    { signals: [{ ...SIGNAL, author_role: "operator" }], reviews: [] },
    { signals: [{ ...SIGNAL, channel: "lab" }], reviews: [] },
    { signals: [{ ...SIGNAL, feedback_id: 7 }], reviews: [] },
    { signals: [Object.fromEntries(Object.entries(SIGNAL).filter(([k]) => k !== "comment"))], reviews: [] }, // a missing field
    { signals: [{ ...Object.fromEntries(Object.entries(SIGNAL).filter(([k]) => k !== "comment")), author_principal: "x" }], reviews: [] }, // a swapped field
    { signals: ["fb"], reviews: [] },
    { signals: [null], reviews: [] },
    { signals: { SIGNAL }, reviews: [] },
    { signals: [SIGNAL] },
    { signals: [], reviews: [{ ...REVIEW, provenance: "teacher" }] },                                     // never a judge's label as human
    { signals: [], reviews: [{ ...REVIEW, request_id: "5c000000-0000-4000-8000-0000000000f2" }] },      // another request's review
    { signals: [], reviews: [{ ...REVIEW, verdict: "great" }] },
    { signals: [], reviews: [{ ...REVIEW, review_id: 1 }] },
    null,
  ];
  for (const body of bad) {
    assert.deepEqual(await reviewFeedback(api(200, body).api, workspace, JOB), { ok: false, reason: "unavailable" }, JSON.stringify(body));
  }
  const judged = { ...SIGNAL, author_role: "judge", channel: "api" };
  assert.deepEqual(await reviewFeedback(api(200, { signals: [judged], reviews: [] }).api, workspace, JOB), { ok: true, entries: [entry(judged)], reviews: [] });
});

const form = (extra: Record<string, unknown> = {}) => ({ request_id: JOB, verdict: "fail", comment: "missed step 3", idempotency_key: KEY, ...extra });

test("C3F-R01 a human review posts the verdict to the request's reviews route with the form's key; provenance is the server's", async () => {
  const w = api(201, REVIEW);
  assert.deepEqual(await submitReview(w.api, workspace, form({ provider_org_id: "b0000009-0000-4000-8000-000000000009", provenance: "teacher", reviewer: "x" })), { ok: true, review: REVIEW });
  assert.deepEqual(w.sent, [{ method: "POST", url: `https://lab-control.example/lab/v1/traces/${JOB}/reviews?provider_org_id=${NEMO}`, key: KEY, auth: "Bearer tok",
    body: { verdict: "fail", comment: "missed step 3" } }]);
  const tied = api(201, { ...REVIEW, run_id: RUN, rubric_version: 2, comment: null });
  await submitReview(tied.api, workspace, form({ comment: "", run_id: RUN, rubric_version: "2" }));
  assert.deepEqual(tied.sent[0].body, { verdict: "fail", run_id: RUN, rubric_version: 2 });
});

test("C3F-R02 a viewer, a malformed field or a missing key is refused before any call", async () => {
  const w = api(201, REVIEW);
  assert.deepEqual(await submitReview(w.api, viewer, form()), { ok: false, reason: "forbidden" });
  for (const bad of [{ request_id: "x" }, { verdict: "great" }, { idempotency_key: undefined }, { idempotency_key: "k" }, { run_id: "r" },
    { rubric_version: "0" }, { rubric_version: "1001" }, { comment: "x".repeat(2001) }]) {
    assert.deepEqual(await submitReview(w.api, workspace, form(bad)), { ok: false, reason: "invalid" }, JSON.stringify(bad));
  }
  assert.equal(w.sent.length, 0);
  assert.deepEqual(await submitReview(null, workspace, form()), { ok: false, reason: "unavailable" });
});

test("C3F-R03 the route's refusals keep their meaning; an unreadable answer is unavailable, never a recorded review", async () => {
  for (const [status, body, reason] of [[404, ERR("not_found"), "not_found"], [403, ERR("forbidden"), "forbidden"], [409, ERR("conflict"), "conflict"],
    [422, ERR("invalid_request"), "invalid"], [503, ERR("unavailable"), "unavailable"], [201, { ...REVIEW, provenance: "judge" }, "unavailable"]] as const) {
    assert.deepEqual(await submitReview(api(status, body).api, workspace, form()), { ok: false, reason }, String(status));
  }
});
