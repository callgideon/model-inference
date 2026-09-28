// WR-R4-1: the HTTP adapter speaks lab-api-2's `/lab/v1/releases` wire exactly (tests/g/lab_releases on
// the Python side): the session token, the actor's provider, the records renamed to the port's keys only,
// the fixed refusals.
import assert from "node:assert/strict";
import test from "node:test";
import { httpReleases } from "../../lib/services/rollouts/http.ts";

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "administrator" as const };
type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answer: (seen: Seen) => Response | Promise<Response>) {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpReleases({ baseUrl: "https://api.test/", token: "eyJ0.a.b", fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const RELEASE = {
  policy_ref: "lab:policy:p", state: "running", fence: 2, plan: { horizon_s: 86400, budget: { unit: "CREDIT", amount: "1" } },
  progress: { assignments: [{ serving_ref: "s", pinned_by: "cohort", requests: 9 }] },
  verdict: { action: "hold", reasons: ["report_inconclusive"], evidence_refs: [], evaluated_at: "t" },
};

test("R4-H01 every call is the session's token and the actor's provider on its route; records come back in the port's keys, values untouched", async () => {
  const { seen, port } = server((s) => s.url.includes("/proposals")
    ? json({ proposal_id: "p1", kind: "expand", policy_ref: "lab:policy:p", fence: 2, state: "proposed", proposed_at: "t", decided_at: null }, 201)
    : json({ data: s.url.includes("optimizations") ? [{ variant_ref: "v", comparison: { performance: { p99_ms_delta: -3 } } }] : { releases: [RELEASE], decisions: [], proposals: [] } }));
  const read = await port.releases(A);
  assert.deepEqual(read, { ok: true, value: { releases: [{
    policyRef: "lab:policy:p", state: "running", fence: 2, plan: { horizonS: 86400, budget: { unit: "CREDIT", amount: "1" } },
    progress: { assignments: [{ servingRef: "s", pinnedBy: "cohort", requests: 9 }] },
    verdict: { action: "hold", reasons: ["report_inconclusive"], evidenceRefs: [], evaluatedAt: "t" } }], decisions: [], proposals: [] } });
  assert.deepEqual(await port.variants(A), { ok: true, value: [{ variantRef: "v", comparison: { performance: { p99MsDelta: -3 } } }] });
  assert.deepEqual(await port.propose(A, "expand", "lab:policy:p", 2), { ok: true, value: {
    proposalId: "p1", kind: "expand", policyRef: "lab:policy:p", fence: 2, state: "proposed", proposedAt: "t", decidedAt: null } });
  const q = `?provider_org_id=${A.providerId}`;
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["GET", `https://api.test/lab/v1/releases${q}`], ["GET", `https://api.test/lab/v1/optimizations${q}`],
    ["POST", `https://api.test/lab/v1/releases/proposals${q}`],
  ]);
  assert.ok(seen.every((s) => s.headers.authorization === "Bearer eyJ0.a.b"));
  assert.deepEqual(seen[2].body, { kind: "expand", policy_ref: "lab:policy:p", fence: 2 });
  assert.equal(seen[2].headers["content-type"], "application/json");
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
