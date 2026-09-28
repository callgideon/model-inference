// B4 swap, real evidence: the journeys J01-J03 rerun through the Lab's evaluation adapter against
// lab-api's `/lab/v1/evaluations` as merged, over the real D7 and L2 on the task-local PostgreSQL
// (`backend.py`, key b3). Skipped unless LAB_B4_REAL=1 (needs Docker and the b3 key):
//   LAB_B4_REAL=1 INFRX_D_TASK=b3 node --test tests/b/stack.test.ts
// Outside the mutant suite (like V1M's stack): its oracle is the real route, not Lab code.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import { httpEvaluation } from "../../lib/services/evaluation/http.ts";
import type { Actor, Launch, SubscriptionRequest } from "../../lib/services/evaluation/port.ts";
import { comparison, runRow, subscriptionRow } from "../../lib/services/evaluation/view.ts";
import { REPORTS } from "./real.ts";

const REAL = process.env.LAB_B4_REAL === "1";
const lab = resolve(import.meta.dirname, "../..");
type Who = "dev" | "viewer" | "other_dev" | "consumer";
type World = { A: string; B: string; dataset: string; harness: string; harness_id: string; external: string; evaluator: string; servings: [string, string]; tokens: Record<Who, string> };

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/b/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
  const line = await new Promise<string>((done, failed) => {
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
      const ready = out.split("\n").find((l) => l.startsWith("READY "));
      if (ready) done(ready);
    });
    child.on("exit", (code) => failed(new Error(`backend exited ${code}: ${out}`)));
  });
  const [, port, world] = line.match(/^READY (\d+) (.*)$/)!;
  return { url: `http://127.0.0.1:${port}`, world: JSON.parse(world), stop: () => child.kill("SIGINT") };
}

test("B4-S01..S03 the evaluation journeys on the real route: launch/progress/cancel/report, foreign and malformed, subscriptions", { skip: !REAL && "LAB_B4_REAL=1 (Docker, b3)" }, async (t) => {
  const { url, world: w, stop } = await backend();
  t.after(stop);
  const as = (who: Who) => httpEvaluation({ baseUrl: url, token: async () => w.tokens[who] });
  const hook = async (path: string, body: unknown) => assert.equal((await fetch(`${url}/_test/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })).status, 200);
  const dev: Actor = { providerId: w.A, role: "developer" };
  const viewer: Actor = { providerId: w.A, role: "viewer" };
  const other: Actor = { providerId: w.B, role: "developer" };
  const LAUNCH: Launch = {
    experiment_id: "33333333-3333-4333-8333-333333333333", dataset_ref: w.dataset, harness_ref: w.harness, evaluator_ref: w.evaluator,
    baseline_serving_ref: w.servings[0], candidate_serving_ref: w.servings[1], seed: 7, max_cases: 3,
    run_limit: { unit: "CREDIT", value: "10.00000000" },
    protocol: { confidence: 0.95, margin: 0.05, min_cases: 2, metric_source: "deterministic_metric", required_slices: { safety: { margin: 0, min_cases: 2 } } },
  };

  await t.test("S01 (J01) launch → two queued D7 runs → progress → cancel → the report, each read back from the records", async () => {
    const catalog = await as("dev").catalog(dev);
    assert.ok(catalog.ok && catalog.value.harnesses[0].harness_id === w.harness_id, JSON.stringify(catalog));
    const launched = await as("dev").launch(dev, LAUNCH);
    assert.ok(launched.ok, JSON.stringify(launched));
    const e = launched.value;
    assert.deepEqual([e.baseline.state, e.candidate.state, e.report], ["queued", "queued", null]);
    assert.deepEqual(e.baseline.cases, { pending: 3 });
    assert.deepEqual(await as("dev").launch(dev, LAUNCH), launched, "the same form twice is the same experiment");
    const runs = await as("dev").runs(dev);
    assert.ok(runs.ok && runs.value.length === 2);
    await hook("state", { run_id: e.candidate.run_id, state: "running" });
    const reread = await as("dev").experiments(dev);
    assert.ok(reread.ok);
    const row = runRow("developer", reread.value[0].candidate);
    assert.deepEqual([row.state, row.open, row.cancel], ["running", "3 / 3 not finished", true]);
    const cancelled = await as("dev").cancel(dev, e.candidate.run_id);
    assert.ok(cancelled.ok && cancelled.value.state === "cancelled");
    assert.deepEqual(await as("dev").cancel(dev, e.candidate.run_id), { ok: false, reason: "conflict" });
    await hook("state", { run_id: e.baseline.run_id, state: "running" });
    await hook("state", { run_id: e.baseline.run_id, state: "succeeded" });
    assert.deepEqual(await as("dev").cancel(dev, e.baseline.run_id), { ok: false, reason: "conflict" }, "a succeeded run is never cancelled");
    const after = await as("dev").runs(dev);
    assert.ok(after.ok);
    assert.deepEqual(after.value.map((r) => [r.run_id, r.state]).sort(), [[e.baseline.run_id, "succeeded"], [e.candidate.run_id, "cancelled"]].sort());
    await hook("settle", { experiment_id: e.experiment_id, report: REPORTS.inconclusive });
    const done = await as("dev").experiments(dev);
    assert.ok(done.ok && done.value[0].report !== null);
    assert.equal(comparison(done.value[0].report!).reasons[0], "coverage 38/40");
  });

  await t.test("S02 (J02) foreign, viewer, consumer-only and malformed variants: nothing leaks, nothing is queued", async () => {
    const e = LAUNCH.experiment_id;
    for (const read of ["runs", "experiments", "subscriptions"] as const) assert.deepEqual(await as("other_dev")[read](other), { ok: true, value: [] }, read);
    assert.deepEqual(await as("other_dev").catalog(other), { ok: true, value: { datasets: [], harnesses: [], servings: [], evaluators: [] } });
    const [base] = ((await as("dev").runs(dev)) as { value: { run_id: string }[] }).value;
    assert.deepEqual(await as("other_dev").cancel(other, base.run_id), { ok: false, reason: "not_found" });
    assert.deepEqual(await as("other_dev").runs(dev), { ok: false, reason: "not_found" }, "not a member of A: A is not found (lab_auth)");
    assert.deepEqual(await as("consumer").runs(dev), { ok: false, reason: "denied" });
    assert.deepEqual(await as("viewer").launch(viewer, { ...LAUNCH, experiment_id: "44444444-4444-4444-8444-444444444444" }), { ok: false, reason: "denied" });
    const viewed = await as("viewer").experiments(viewer);
    assert.ok(viewed.ok && viewed.value.length === 1, "every role reads");
    const foreign = w.dataset.replace(w.A, w.B);
    for (const bad of [{ dataset_ref: foreign }, { candidate_serving_ref: w.servings[1].replace(/@sha256:.*/, `@sha256:${"8".repeat(64)}`) }])
      assert.deepEqual(await as("dev").launch(dev, { ...LAUNCH, ...bad, experiment_id: "55555555-5555-4555-8555-555555555555" }), { ok: false, reason: "invalid" }, JSON.stringify(bad));
    assert.deepEqual(await as("dev").launch(dev, { ...LAUNCH, seed: 8 }), { ok: false, reason: "conflict" }, "one id, another body");
    assert.deepEqual(await as("dev").launch(dev, { ...LAUNCH, experiment_id: "not-a-uuid" }), { ok: false, reason: "invalid" });
    const runs = await as("dev").runs(dev);
    assert.ok(runs.ok && runs.value.length === 2, `still only ${e}'s two runs`);
  });

  await t.test("S03 (J03) a checkpoint subscription pins its suite and CREDIT limits once; decisions arrive from B3 and stay the provider's", async () => {
    const SUB: SubscriptionRequest = {
      subscription_id: "66666666-6666-4666-8666-666666666666", external_run_ref: w.external, dataset_ref: w.dataset, harness_ref: w.harness,
      evaluator_ref: w.evaluator, seed: 7, max_cases: 3, run_limit: { unit: "CREDIT", value: "5.00000000" }, limit: { unit: "CREDIT", value: "20.00000000" },
      max_active: 2, policy: "latest_only",
    };
    const made = await as("dev").subscribe(dev, SUB);
    assert.ok(made.ok, JSON.stringify(made));
    assert.deepEqual(await as("dev").subscribe(dev, SUB), made, "the same form twice is the same subscription");
    assert.deepEqual(await as("dev").subscribe(dev, { ...SUB, seed: 9 }), { ok: false, reason: "conflict" });
    assert.deepEqual(await as("viewer").subscribe(viewer, { ...SUB, subscription_id: "77777777-7777-4777-8777-777777777777" }), { ok: false, reason: "denied" });
    const again = { ...SUB, subscription_id: "88888888-8888-4888-8888-888888888888" };
    assert.deepEqual(await as("dev").subscribe(dev, { ...again, run_limit: { unit: "CREDIT", value: "30.00000000" } }), { ok: false, reason: "invalid" });
    assert.deepEqual(await as("dev").subscribe(dev, { ...again, limit: { unit: "PROVIDER_USD", value: "20.00000000" } }), { ok: false, reason: "invalid" });
    assert.deepEqual(await as("dev").subscribe(dev, { ...again, external_run_ref: w.external.replace(w.A, w.B) }), { ok: false, reason: "invalid" });
    await hook("decide", { subscription_id: SUB.subscription_id, checkpoint_id: "c1", decision: { state: "queued", reason: null, run_id: "r1" } });
    const mine = await as("dev").subscriptions(dev);
    assert.ok(mine.ok, JSON.stringify(mine));
    assert.deepEqual(subscriptionRow(mine.value[0]).decisions.map((d) => d.outcome), ["evaluation queued · run r1"]);
    assert.deepEqual(await as("other_dev").subscriptions(other), { ok: true, value: [] });
  });
});
