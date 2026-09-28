// B4 journeys on the LAB_EVALS stand-in (the WR-B4-1 shape): launch queues backend runs and the page
// learns everything else from the records; foreign, viewer and malformed variants are refused.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeEvaluation } from "../../lib/services/evaluation/fake.ts";
import type { Actor, Launch, SubscriptionRequest } from "../../lib/services/evaluation/port.ts";
import { comparison, runRow, subscriptionRow } from "../../lib/services/evaluation/view.ts";
import { REPORTS } from "./real.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const ref = (kind: string, provider: string, n: number) => `lab:${kind}:${provider}:0000000${n}-0000-4000-8000-00000000000${n}@sha256:${String(n).repeat(64)}`;
const dev: Actor = { providerId: A, role: "developer" };
const viewer: Actor = { providerId: A, role: "viewer" };
const other: Actor = { providerId: B, role: "administrator" };
const world = () => {
  const f = new FakeEvaluation();
  f.offer(A, {
    datasets: [{ ref: ref("dataset", A, 1), label: "support-v1" }],
    harnesses: [{ ref: ref("harness", A, 2), harness_id: "h", version: 3, adapter: "text" }],
    servings: [{ ref: ref("serving", A, 3), label: "base" }, { ref: ref("serving", A, 4), label: "tuned" }],
    evaluators: [{ ref: ref("evaluator", A, 5), label: "exact match" }],
  });
  return f;
};
const LAUNCH: Launch = {
  experiment_id: "33333333-3333-4333-8333-333333333333", dataset_ref: ref("dataset", A, 1), harness_ref: ref("harness", A, 2),
  evaluator_ref: ref("evaluator", A, 5), baseline_serving_ref: ref("serving", A, 3), candidate_serving_ref: ref("serving", A, 4),
  seed: 7, max_cases: 40, run_limit: { unit: "CREDIT", value: "10.00000000" },
  protocol: { confidence: 0.95, margin: 0.05, min_cases: 10, metric_source: "deterministic_metric", required_slices: { safety: { margin: 0, min_cases: 5 } } },
};

test("B4-J01 launch → queued baseline and candidate runs → progress → cancel → the report, each read back from the records", async () => {
  const f = world();
  const launched = await f.launch(dev, LAUNCH);
  assert.ok(launched.ok);
  const e = launched.value;
  assert.deepEqual([e.baseline.state, e.candidate.state, e.report], ["queued", "queued", null]);
  assert.deepEqual(e.baseline.cases, { pending: 40 });
  assert.deepEqual(await f.launch(dev, LAUNCH), launched, "the same form twice is the same experiment");
  assert.equal((await f.runs(dev)).ok && (await f.runs(dev) as { value: unknown[] }).value.length, 2);
  f.progress(e.candidate.run_id, { state: "running", cases: { done: 10, failed: 2, leased: 1, pending: 27 }, costs: { CREDIT: "1.20000000" } });
  const reread = await f.experiments(dev);
  assert.ok(reread.ok);
  assert.equal(runRow("developer", reread.value[0].candidate).done, "10 / 40 done");
  const cancelled = await f.cancel(dev, e.candidate.run_id);
  assert.ok(cancelled.ok && cancelled.value.state === "cancelled");
  assert.deepEqual(await f.cancel(dev, e.candidate.run_id), { ok: false, reason: "conflict" });
  f.settle(e.experiment_id, REPORTS.inconclusive);
  const done = await f.experiments(dev);
  assert.ok(done.ok && done.value[0].report !== null);
  assert.equal(comparison(done.value[0].report!).reasons[0], "coverage 38/40");
});

test("B4-J02 foreign, viewer and malformed variants: nothing leaks, nothing is queued", async () => {
  const f = world();
  const launched = await f.launch(dev, LAUNCH);
  assert.ok(launched.ok);
  for (const read of [f.runs(other), f.experiments(other), f.subscriptions(other)]) assert.deepEqual(await read, { ok: true, value: [] });
  assert.deepEqual(await f.catalog(other), { ok: true, value: { datasets: [], harnesses: [], servings: [], evaluators: [] } });
  assert.deepEqual(await f.cancel(other, launched.value.baseline.run_id), { ok: false, reason: "not_found" });
  assert.deepEqual(await f.cancel({ ...other, role: "viewer" }, launched.value.baseline.run_id), { ok: false, reason: "not_found" });
  assert.deepEqual(await f.cancel(viewer, launched.value.baseline.run_id), { ok: false, reason: "denied" });
  assert.deepEqual(await f.launch(viewer, { ...LAUNCH, experiment_id: "44444444-4444-4444-8444-444444444444" }), { ok: false, reason: "denied" });
  for (const bad of [{ dataset_ref: ref("dataset", B, 1) }, { harness_ref: ref("harness", A, 9) }, { candidate_serving_ref: ref("serving", A, 8) },
    { evaluator_ref: ref("evaluator", A, 7) }, { baseline_serving_ref: ref("serving", B, 3) }])
    assert.deepEqual(await f.launch(dev, { ...LAUNCH, ...bad, experiment_id: "55555555-5555-4555-8555-555555555555" }), { ok: false, reason: "invalid" }, JSON.stringify(bad));
  assert.deepEqual(await f.launch(dev, { ...LAUNCH, seed: 8 }), { ok: false, reason: "conflict" }, "one id, another body");
  assert.deepEqual(await f.launch(other, LAUNCH), { ok: false, reason: "invalid" }, "another provider reusing the id reaches nothing of A's");
  const runs = await f.runs(dev);
  assert.ok(runs.ok && runs.value.length === 2);
});

const SUB: SubscriptionRequest = {
  subscription_id: "66666666-6666-4666-8666-666666666666", external_run_ref: ref("external_run", A, 6), dataset_ref: ref("dataset", A, 1),
  harness_ref: ref("harness", A, 2), evaluator_ref: ref("evaluator", A, 5), seed: 7, max_cases: 40,
  run_limit: { unit: "CREDIT", value: "5.00000000" }, limit: { unit: "CREDIT", value: "20.00000000" }, max_active: 2, policy: "latest_only",
};

test("B4-J03 a checkpoint subscription pins its suite and CREDIT limits once; decisions arrive from B3 and stay the provider's", async () => {
  const f = world();
  const made = await f.subscribe(dev, SUB);
  assert.ok(made.ok);
  assert.deepEqual(await f.subscribe(dev, SUB), made, "the same form twice is the same subscription");
  assert.deepEqual(await f.subscribe(dev, { ...SUB, seed: 9 }), { ok: false, reason: "conflict" });
  assert.deepEqual(await f.subscribe(viewer, { ...SUB, subscription_id: "77777777-7777-4777-8777-777777777777" }), { ok: false, reason: "denied" });
  const again = { ...SUB, subscription_id: "88888888-8888-4888-8888-888888888888" };
  assert.deepEqual(await f.subscribe(dev, { ...again, run_limit: { unit: "CREDIT", value: "30.00000000" } }), { ok: false, reason: "invalid" });
  assert.deepEqual(await f.subscribe(dev, { ...again, limit: { unit: "PROVIDER_USD", value: "20.00000000" } }), { ok: false, reason: "invalid" });
  assert.deepEqual(await f.subscribe(dev, { ...again, external_run_ref: ref("external_run", B, 6) }), { ok: false, reason: "invalid" });
  f.decide(SUB.subscription_id, { checkpoint_id: "c1", step: 10, receipt: "evaluated", state: "queued", reason: null, run_id: "r1" });
  const mine = await f.subscriptions(dev);
  assert.ok(mine.ok);
  assert.deepEqual(subscriptionRow(mine.value[0]).decisions.map((d) => d.outcome), ["evaluation queued · run r1"]);
  assert.deepEqual(await f.subscriptions(other), { ok: true, value: [] });
});
