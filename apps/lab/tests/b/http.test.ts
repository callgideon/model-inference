// WR-B4-1: the HTTP adapter speaks lab-api-2's `/lab/v1/evaluations` wire exactly (tests/g/lab_evaluations
// on the Python side): the session token, the actor's provider, the verbatim records, the fixed refusals.
import assert from "node:assert/strict";
import test from "node:test";
import { httpEvaluation } from "../../lib/services/evaluation/http.ts";

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "developer" as const };
type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answer: (seen: Seen) => Response | Promise<Response>) {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpEvaluation({ baseUrl: "https://api.test/", token: "eyJ0.a.b", fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

test("B4-H01 every call is the session's token and the actor's provider on its route, reads unwrapped from {data}", async () => {
  const { seen, port } = server((s) => json(s.method === "GET" ? { data: [{ run_id: "r1" }] } : { run_id: "r1", state: "cancelled" }));
  assert.deepEqual(await port.runs(A), { ok: true, value: [{ run_id: "r1" }] });
  assert.deepEqual(await port.cancel(A, "r 1"), { ok: true, value: { run_id: "r1", state: "cancelled" } });
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
