// J3 / WR-V3-1 JUDGE-SCORES, AP-09 09c (register row 98): the per-request judge read behind V3's
// JudgePort over api-judge-2's `GET /lab/v1/traces/{request_id}/judge-runs` (WR-AP09L-3). The fake API
// stands for the route and records what was sent. The port asks for the guarded workspace only, maps
// each TraceJudgeRun to V3's JudgeRun (typed scores, PROVIDER_USD exact strings, the stored
// calibration) and fails closed: a viewer is denied before any call, a refusal keeps its meaning, and
// anything it cannot read whole is unavailable, never a partial or invented history.
import assert from "node:assert/strict";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import { judgeRunsPort } from "../../../lib/services/judge/runs.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const REQ = "c0000000-0000-4000-8000-0000000000c1";
const RUN = "e0000000-0000-4000-8000-00000000000e";
const dev = { providerId: A, role: "developer" as const };
const ERR = (code: string) => ({ error: { code, message: "m", request_id: "q", retryable: false } });
const CAL = { state: "insufficient", labels: 3, required: 30, agreement: null, interval: null };
const WIRE = {
  run_id: RUN, state: "collected", judge_model: "claude-opus-5", rubric_version: 2, media: true,
  reserved: { amount: "0.40000000", unit: "PROVIDER_USD" }, actual: { amount: "0.12500000", unit: "PROVIDER_USD" },
  scores: [{ criterion: "accuracy", score: 4, max_score: 5, requires_media: true }], overall_pass: true, calibration: CAL,
};
const MAPPED = {
  runId: RUN, mode: "live", state: "collected", judgeModel: "claude-opus-5", rubricVersion: 2, media: true,
  reservedUsd: "0.40000000", actualUsd: "0.12500000",
  scores: [{ criterion: "accuracy", score: 4, max: 5, requiresMedia: true }], overallPass: true, calibration: CAL,
};

function server(answer: { status?: number; body?: unknown; throws?: boolean } = {}) {
  const sent: { path: string; query: Record<string, string>; auth: string | null }[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const u = new URL(url);
    sent.push({ path: u.pathname, query: Object.fromEntries(u.searchParams), auth: new Headers(init.headers).get("authorization") });
    if (answer.throws) throw new Error("transport");
    return new Response(JSON.stringify(answer.body ?? { data: [WIRE], next_cursor: null }), { status: answer.status ?? 200 });
  }) as typeof globalThis.fetch;
  return { api: labApi({ baseUrl: "https://lab-control.example", fetch, session: () => ({ token: "tok" }) }), sent };
}

test("J3L-R01 a developer reads this request's runs for the guarded workspace only, mapped to V3's live runs", async () => {
  const s = server();
  assert.deepEqual(await judgeRunsPort(s.api).runs(dev, REQ), { ok: true, value: [MAPPED] });
  assert.deepEqual(s.sent, [{ path: `/lab/v1/traces/${REQ}/judge-runs`, query: { provider_org_id: A }, auth: "Bearer tok" }]);
  const open = { ...WIRE, state: "submitted", actual: null, scores: [], overall_pass: null, media: false, calibration: { ...CAL, state: "calibrated", labels: 42, agreement: 0.82, interval: [0.71, 0.9] } };
  const { overall_pass: _, ...noVerdict } = open; // an absent optional reads as null, as the schema says
  for (const wire of [open, noVerdict])
    assert.deepEqual(await judgeRunsPort(server({ body: { data: [wire] } }).api).runs({ providerId: A, role: "administrator" as const }, REQ), {
      ok: true,
      value: [{ ...MAPPED, state: "submitted", actualUsd: null, scores: [], overallPass: null, media: false, calibration: open.calibration }],
    });
  assert.deepEqual(await judgeRunsPort(server({ body: { data: [] } }).api).runs(dev, REQ), { ok: true, value: [] });
});

test("J3L-R02 a viewer is denied and a misconfigured Lab is unavailable, before any call", async () => {
  const s = server();
  assert.deepEqual(await judgeRunsPort(s.api).runs({ providerId: A, role: "viewer" as const }, REQ), { ok: false, reason: "denied" });
  assert.deepEqual(await judgeRunsPort(null).runs(dev, REQ), { ok: false, reason: "unavailable" });
  assert.equal(s.sent.length, 0);
});

test("J3L-R03 the route's refusals keep their meaning: 401/403/404 denied; an outage, a lost answer or a 409/422 unavailable", async () => {
  for (const status of [401, 403, 404])
    assert.deepEqual(await judgeRunsPort(server({ status, body: ERR("forbidden") }).api).runs(dev, REQ), { ok: false, reason: "denied" }, String(status));
  for (const status of [409, 422, 500, 503])
    assert.deepEqual(await judgeRunsPort(server({ status, body: ERR("x") }).api).runs(dev, REQ), { ok: false, reason: "unavailable" }, String(status));
  assert.deepEqual(await judgeRunsPort(server({ throws: true }).api).runs(dev, REQ), { ok: false, reason: "unavailable" });
});

test("J3L-R04 an answer it cannot read whole is unavailable: never CREDIT as PROVIDER_USD, never a partial history", async () => {
  const bad: [string, unknown][] = [
    ["not a page", [WIRE]],
    ["a further page", { data: [WIRE], next_cursor: "c2" }],
    ["reserved in CREDIT", { data: [{ ...WIRE, reserved: { amount: "0.40000000", unit: "CREDIT" } }] }],
    ["actual in USD", { data: [{ ...WIRE, actual: { amount: "0.12500000", unit: "USD" } }] }],
    ["no reserved amount", { data: [{ ...WIRE, reserved: null }] }],
    ["an inexact amount", { data: [{ ...WIRE, reserved: { amount: "0.4", unit: "PROVIDER_USD" } }] }],
    ["an unknown state", { data: [{ ...WIRE, state: "estimated" }] }],
    ["a fractional score", { data: [{ ...WIRE, scores: [{ ...WIRE.scores[0], score: 3.5 }] }] }],
    ["a score without its media flag", { data: [{ ...WIRE, scores: [{ criterion: "accuracy", score: 4, max_score: 5 }] }] }],
    ["an unknown calibration state", { data: [{ ...WIRE, calibration: { ...CAL, state: "scored" } }] }],
    ["a text rubric version", { data: [{ ...WIRE, rubric_version: "2" }] }],
    ["a text verdict", { data: [{ ...WIRE, overall_pass: "true" }] }],
    ["a second, broken row", { data: [WIRE, { ...WIRE, run_id: 7 }] }],
  ];
  for (const [why, body] of bad)
    assert.deepEqual(await judgeRunsPort(server({ body }).api).runs(dev, REQ), { ok: false, reason: "unavailable" }, why);
});
