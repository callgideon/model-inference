// WR-R4-1: the HTTP adapter speaks lab-api-2's `/lab/v1/releases` wire exactly (tests/g/lab_releases on
// the Python side): the session token, the actor's provider, the records renamed to the port's keys only,
// the fixed refusals, and (the swap) a record the pages cannot read fails the whole answer closed.
import assert from "node:assert/strict";
import test from "node:test";
import { httpReleases } from "../../lib/services/rollouts/http.ts";
import { EXPAND, proposal, release, variant } from "./fixtures.ts";

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "administrator" as const };
type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answer: (seen: Seen) => Response | Promise<Response>, token: string | null = "eyJ0.a.b") {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpReleases({ baseUrl: "https://api.test/", token: async () => token, fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
/** The route's snake_case for a port fixture (the test's own rename, not the adapter's). */
const snake = (v: unknown): unknown => Array.isArray(v) ? v.map(snake)
  : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`), snake(x)])) : v;
const RELEASE = release();
const PROPOSAL = proposal({ kind: "expand" });
const DECISION = { policyRef: RELEASE.policyRef, decision: "hold", reasons: ["min_requests"], evidenceRefs: [], decidedBy: "operator", decidedAt: "t" };
const VARIANT = variant();

test("R4-H01 every call is the session's token and the actor's provider on its route; records come back in the port's keys, values untouched", async () => {
  const records = { releases: [RELEASE, release({ progress: null, verdict: EXPAND })], decisions: [DECISION], proposals: [PROPOSAL] };
  const { seen, port } = server((s) => s.url.includes("/proposals") ? json(snake(PROPOSAL), 201)
    : json({ data: snake(s.url.includes("optimizations") ? [VARIANT, { ...VARIANT, comparison: null }] : records) }));
  assert.deepEqual(await port.releases(A), { ok: true, value: records });
  assert.deepEqual(await port.variants(A), { ok: true, value: [VARIANT, { ...VARIANT, comparison: null }] });
  assert.deepEqual(await port.propose(A, "expand", PROPOSAL.policyRef, 3), { ok: true, value: PROPOSAL });
  const q = `?provider_org_id=${A.providerId}`;
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["GET", `https://api.test/lab/v1/releases${q}`], ["GET", `https://api.test/lab/v1/optimizations${q}`],
    ["POST", `https://api.test/lab/v1/releases/proposals${q}`],
  ]);
  assert.ok(seen.every((s) => s.headers.authorization === "Bearer eyJ0.a.b"));
  assert.deepEqual(seen[2].body, { kind: "expand", policy_ref: PROPOSAL.policyRef, fence: 3 });
  assert.equal(seen[2].headers["content-type"], "application/json");
  assert.equal((snake(RELEASE) as { plan: Record<string, unknown> }).plan.max_p99_ms, 4000, "the route's own key names");
});

test("R4-H02 the route's refusals are the port's reasons; anything else, or no answer, is unavailable", async () => {
  const cases: [number, string][] = [[401, "denied"], [403, "denied"], [404, "not_found"], [409, "conflict"], [422, "invalid"], [410, "unavailable"], [503, "unavailable"], [500, "unavailable"]];
  for (const [status, reason] of cases) {
    const { port } = server(() => json({ refusal: "x" }, status));
    assert.deepEqual(await port.propose(A, "rollback", "lab:policy:p", 1), { ok: false, reason }, String(status));
  }
  const down = server(() => { throw new TypeError("fetch failed"); });
  assert.deepEqual(await down.port.releases(A), { ok: false, reason: "unavailable" });
  const garbled = server(() => new Response("<html>", { status: 200 }));
  assert.deepEqual(await garbled.port.variants(A), { ok: false, reason: "unavailable" });
});

test("R4-H03 one unreadable release, decision, proposal or variant fails the whole answer closed", async () => {
  const answers = (body: unknown) => server(() => json(body)).port;
  const drop = (o: object, key: string) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== key));
  const records = (over: object) => ({ data: snake({ releases: [RELEASE], decisions: [DECISION], proposals: [PROPOSAL], ...over }) });
  const unreadable = [
    { releases: [RELEASE, { ...RELEASE, state: "paused" }] }, { releases: [drop(RELEASE, "fence")] }, { releases: [{ ...RELEASE, plan: drop(RELEASE.plan, "budget") }] },
    { releases: [{ ...RELEASE, plan: { ...RELEASE.plan, budget: { amount: 50, unit: "PROVIDER_USD" } } }] },
    { releases: [{ ...RELEASE, progress: { ...RELEASE.progress, candidate: { requests: 1, errors: 0 } } }] },
    { releases: [{ ...RELEASE, progress: { ...RELEASE.progress, assignments: [{ servingRef: "s", pinnedBy: "random", requests: 1 }] } }] },
    { releases: [{ ...RELEASE, verdict: { ...RELEASE.verdict, action: "promote" } }] }, { releases: [drop(RELEASE, "verdict")] },
    { releases: [{ ...RELEASE, candidates: [{ servingRef: "s" }] }] }, { releases: [{ ...RELEASE, mode: "full" }] },
    { decisions: [{ ...DECISION, decision: "approve" }] }, { decisions: [drop(DECISION, "decidedAt")] },
    { proposals: [{ ...PROPOSAL, state: "pending" }] }, { proposals: [drop(PROPOSAL, "fence")] }, { proposals: undefined },
  ];
  for (const over of unreadable) assert.deepEqual(await answers(records(over)).releases(A), { ok: false, reason: "unavailable" }, JSON.stringify(over).slice(0, 100));
  const variants = [
    { ...VARIANT, comparison: { ...VARIANT.comparison, outcome: "better" } }, drop(VARIANT, "baseServingRef"), drop(VARIANT, "variantServingRef"),
    { ...VARIANT, variant: drop(VARIANT.variant!, "quantization") },
    { ...VARIANT, comparison: { ...VARIANT.comparison, performance: { ...VARIANT.comparison!.performance, throughputRatio: "1.4" } } },
    { ...VARIANT, comparison: drop(VARIANT.comparison!, "optimizationClaimed") },
  ];
  for (const v of variants) assert.deepEqual(await answers({ data: snake([VARIANT, v]) }).variants(A), { ok: false, reason: "unavailable" }, JSON.stringify(v).slice(0, 100));
  assert.deepEqual(await answers({ data: snake(VARIANT) }).variants(A), { ok: false, reason: "unavailable" }, "not a list");
  assert.deepEqual(await answers(snake(drop(PROPOSAL, "proposalId"))).propose(A, "rollback", "p", 1), { ok: false, reason: "unavailable" });
});

test("R4-H06 a variant whose identities R3 has not persisted (absent or null) still lists, its serving refs intact (R252)", async () => {
  const bare = Object.fromEntries(Object.entries(VARIANT).filter(([k]) => k !== "base" && k !== "variant"));
  const listed = [bare, { ...VARIANT, base: null, variant: null }];
  assert.deepEqual(await server(() => json({ data: snake(listed) })).port.variants(A), { ok: true, value: listed });
});

test("R4-H04 without a session token nothing is sent and every call is unavailable", async () => {
  const { seen, port } = server(() => json({ data: [] }), null);
  for (const call of [port.releases(A), port.variants(A), port.propose(A, "rollback", "p", 1)]) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  assert.equal(seen.length, 0);
});

test("R4-H05 a session-token getter that rejects is no session: nothing is sent and every call is unavailable", async () => {
  const seen: string[] = [];
  const fetch = (async (url: string) => { seen.push(url); return json({ data: [] }); }) as unknown as typeof globalThis.fetch;
  const port = httpReleases({ baseUrl: "https://api.test/", token: async () => { throw new Error("session store unreadable"); }, fetch });
  const calls = [port.releases(A), port.variants(A), port.propose(A, "rollback", "p", 1)].map((call) => call.catch((e: unknown) => ({ threw: String(e) })));
  for (const call of calls) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  assert.equal(seen.length, 0);
});
