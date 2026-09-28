// WR-B4-1: the HTTP adapter speaks lab-api-2's `/lab/v1/evaluations` wire exactly (tests/g/lab_evaluations
// on the Python side): the session token, the actor's provider, the verbatim records, the fixed refusals,
// and (the swap) a record it cannot read fails the whole answer closed instead of reaching a page.
import assert from "node:assert/strict";
import test from "node:test";
import { httpEvaluation } from "../../lib/services/evaluation/http.ts";
import { REPORTS, RUNS } from "./real.ts";

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "developer" as const };
type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answer: (seen: Seen) => Response | Promise<Response>, token: string | null = "eyJ0.a.b") {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpEvaluation({ baseUrl: "https://api.test/", token: async () => token, fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

const ref = (kind: string) => `lab:${kind}:${A.providerId}:00000001-0000-4000-8000-000000000001@sha256:${"1".repeat(64)}`;
const EXPERIMENT = { experiment_id: "e1", created_at: "2026-09-28T10:00:00Z", protocol: REPORTS.reject.protocol, baseline: RUNS.succeeded, candidate: RUNS.cancelled, report: REPORTS.reject };
const CATALOG = {
  datasets: [{ ref: ref("dataset"), label: "d" }], harnesses: [{ ref: ref("harness"), harness_id: "h", version: 1, adapter: "text" }],
  servings: [{ ref: ref("serving"), label: "s" }], evaluators: [{ ref: ref("evaluator"), label: "e" }],
};
const SUBSCRIPTION = {
  subscription_id: "s1", external_run_ref: ref("external_run"), dataset_ref: ref("dataset"), harness_ref: ref("harness"), evaluator_ref: ref("evaluator"),
  seed: 7, max_cases: 4, run_limit: { unit: "CREDIT", value: "1.00000000" }, limit: { unit: "CREDIT", value: "4.00000000" }, max_active: 1, policy: "every",
  decisions: [{ checkpoint_id: "c1", step: 1, receipt: "evaluated", state: "queued", reason: null, run_id: "r1" }],
};

test("B4-H01 every call is the session's token and the actor's provider on its route, reads unwrapped from {data}", async () => {
  const { seen, port } = server((s) => json(s.method === "GET" ? { data: [RUNS.succeeded] } : RUNS.cancelled));
  assert.deepEqual(await port.runs(A), { ok: true, value: [RUNS.succeeded] });
  assert.deepEqual(await port.cancel(A, "r 1"), { ok: true, value: RUNS.cancelled });
  const launch = { experiment_id: "e1", protocol: { required_slices: { safety: { margin: 0, min_cases: 2 } } } };
  await port.launch(A, launch as never);
  await port.subscribe(A, { subscription_id: "s1" } as never);
  for (const read of ["catalog", "experiments", "subscriptions"] as const) await port[read](A);
  const q = `?provider_org_id=${A.providerId}`;
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["GET", `https://api.test/lab/v1/evaluations/runs${q}`], ["POST", `https://api.test/lab/v1/evaluations/runs/r%201/cancel${q}`],
    ["POST", `https://api.test/lab/v1/evaluations/experiments${q}`], ["POST", `https://api.test/lab/v1/evaluations/subscriptions${q}`],
    ["GET", `https://api.test/lab/v1/evaluations/catalog${q}`], ["GET", `https://api.test/lab/v1/evaluations/experiments${q}`],
    ["GET", `https://api.test/lab/v1/evaluations/subscriptions${q}`],
  ]);
  assert.ok(seen.every((s) => s.headers.authorization === "Bearer eyJ0.a.b"));
  assert.equal(seen[1].body, undefined);
  assert.equal(JSON.stringify(seen[2].body), JSON.stringify(launch));
  assert.equal(seen[2].headers["content-type"], "application/json");
});

test("B4-H02 the route's refusals are the port's reasons; anything else, or no answer, is unavailable", async () => {
  const cases: [number, string][] = [[401, "denied"], [403, "denied"], [404, "not_found"], [409, "conflict"], [422, "invalid"], [410, "unavailable"], [503, "unavailable"], [500, "unavailable"]];
  for (const [status, reason] of cases) {
    const { port } = server(() => json({ refusal: "x" }, status));
    assert.deepEqual(await port.cancel(A, "r1"), { ok: false, reason }, String(status));
  }
  const down = server(() => { throw new TypeError("fetch failed"); });
  assert.deepEqual(await down.port.runs(A), { ok: false, reason: "unavailable" });
  const garbled = server(() => new Response("<html>", { status: 200 }));
  assert.deepEqual(await garbled.port.experiments(A), { ok: false, reason: "unavailable" });
});

test("B4-H03 the backends' records pass verbatim; one unreadable row fails the whole answer closed", async () => {
  const answers = (body: unknown) => server(() => json(body)).port;
  const readable = [
    ["runs", { data: [RUNS.succeeded, RUNS.queued] }], ["experiments", { data: [EXPERIMENT, { ...EXPERIMENT, report: null }] }],
    ["catalog", { data: CATALOG }], ["subscriptions", { data: [SUBSCRIPTION] }],
  ] as const;
  for (const [read, body] of readable) assert.deepEqual(await answers(body)[read](A), { ok: true, value: body.data }, read);
  const drop = (o: object, key: string) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== key));
  const unreadable = [
    ["runs", { data: [RUNS.succeeded, { ...RUNS.queued, state: "paused" }] }], ["runs", { data: [drop(RUNS.queued, "cases")] }],
    ["runs", { data: [{ ...RUNS.queued, costs: { CREDIT: 1.5 } }] }], ["runs", { data: RUNS.queued }], ["runs", {}],
    ["experiments", { data: [{ ...EXPERIMENT, report: drop(REPORTS.reject, "decision") }] }],
    ["experiments", { data: [{ ...EXPERIMENT, report: { ...REPORTS.reject, decision: { outcome: "maybe", reasons: [] } } }] }],
    ["experiments", { data: [{ ...EXPERIMENT, candidate: null }] }], ["experiments", { data: [drop(EXPERIMENT, "protocol")] }],
    ["experiments", { data: [drop(EXPERIMENT, "report")] }],
    ["catalog", { data: { ...CATALOG, harnesses: [{ ref: ref("harness") }] } }], ["catalog", { data: drop(CATALOG, "servings") }],
    ["subscriptions", { data: [{ ...SUBSCRIPTION, decisions: [{ checkpoint_id: "c1" }] }] }], ["subscriptions", { data: [{ ...SUBSCRIPTION, limit: { value: "4.00000000" } }] }],
  ] as const;
  for (const [read, body] of unreadable) assert.deepEqual(await answers(body)[read](A), { ok: false, reason: "unavailable" }, `${read} ${JSON.stringify(body).slice(0, 80)}`);
  assert.deepEqual(await answers(drop(RUNS.cancelled, "run_id")).cancel(A, "r1"), { ok: false, reason: "unavailable" });
  assert.deepEqual(await answers({ ...EXPERIMENT, baseline: {} }).launch(A, {} as never), { ok: false, reason: "unavailable" });
  assert.deepEqual(await answers(drop(SUBSCRIPTION, "decisions")).subscribe(A, {} as never), { ok: false, reason: "unavailable" });
  assert.deepEqual(await answers(EXPERIMENT).launch(A, {} as never), { ok: true, value: EXPERIMENT });
  assert.deepEqual(await answers(SUBSCRIPTION).subscribe(A, {} as never), { ok: true, value: SUBSCRIPTION });
});

test("B4-H04 without a session token nothing is sent and every call is unavailable", async () => {
  const { seen, port } = server(() => json({ data: [] }), null);
  for (const call of [port.runs(A), port.cancel(A, "r1"), port.launch(A, {} as never)]) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  assert.equal(seen.length, 0);
});

test("B4-H05 a session-token getter that rejects is no session: nothing is sent and every call is unavailable", async () => {
  const seen: string[] = [];
  const fetch = (async (url: string) => { seen.push(url); return json({ data: [] }); }) as unknown as typeof globalThis.fetch;
  const port = httpEvaluation({ baseUrl: "https://api.test/", token: async () => { throw new Error("session store unreadable"); }, fetch });
  const calls = [port.runs(A), port.cancel(A, "r1"), port.launch(A, {} as never)].map((call) => call.catch((e: unknown) => ({ threw: String(e) })));
  for (const call of calls) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  assert.equal(seen.length, 0);
});
