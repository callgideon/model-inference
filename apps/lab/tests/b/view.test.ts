// B4: what a provider sees and may do about evaluation runs, from the records only (D7's run status,
// B2's report, B3's decisions): denominators, units kept apart, and a port that fails closed.
import assert from "node:assert/strict";
import test from "node:test";
import { evaluationPort, holds, isPreview, type Report, type Run, type Subscription } from "../../lib/services/evaluation/port.ts";
import { comparison, refusalCopy, REFUSAL_COPY, runRow, subscriptionRow, units } from "../../lib/services/evaluation/view.ts";
import { REPORTS, same } from "./real.ts";

const run = (over: Partial<Run> = {}): Run => ({
  run_id: "r1", run_ref: "lab:run:x", dataset_ref: "lab:dataset:x", state: "running",
  cases: { done: 2, failed: 1, leased: 1, pending: 6 }, attempts: { succeeded: 2, failed: 1, expired: 1, leased: 1 },
  costs: { CREDIT: "3.12000000" }, ...over,
});

test("B4-V01 everyone in the workspace reads evaluations; only developer and administrator run or cancel them", () => {
  const table = (["viewer", "developer", "administrator"] as const).map((r) => [holds(r, "read_aggregate_health"), holds(r, "run_evaluation")]);
  assert.deepEqual(table, [[true, false], [true, true], [true, true]]);
});

test("B4-V02 progress is counted against every case of the run, failures and unfinished cases included", () => {
  const r = runRow("viewer", run());
  assert.deepEqual([r.done, r.failed, r.skipped, r.open], ["2 / 10 done", "1 / 10 failed", "0 / 10 skipped", "7 / 10 not finished"]);
  assert.equal(r.attempts, "expired 1 · failed 1 · leased 1 · succeeded 2");
  assert.equal(runRow("viewer", run({ attempts: {} })).attempts, "none yet");
});

test("B4-V03 cancel is offered only on a queued or running run, and only to a role that runs evaluations", () => {
  const cancel = (role: "viewer" | "developer" | "administrator", state: Run["state"]) => runRow(role, run({ state })).cancel;
  assert.deepEqual((["queued", "running", "succeeded", "failed", "cancelled"] as const).map((s) => cancel("developer", s)), [true, true, false, false, false]);
  assert.equal(cancel("viewer", "running"), false);
  assert.equal(cancel("administrator", "queued"), true);
});

test("B4-V04 a run's state is its record's: queued waits for a worker and an ended run is never called a success", () => {
  assert.equal(runRow("viewer", run({ state: "queued" })).state, "queued · waiting for a worker");
  assert.equal(runRow("viewer", run({ state: "succeeded" })).state, "finished");
  assert.equal(runRow("viewer", run({ state: "cancelled" })).state, "cancelled");
});

test("B4-V05 CREDIT and PROVIDER_USD are listed per unit, as exact strings, never summed or converted", () => {
  assert.deepEqual(units({ PROVIDER_USD: "0.25000000", CREDIT: "1.50000000" }), ["1.50000000 CREDIT", "0.25000000 PROVIDER_USD"]);
  assert.deepEqual(units({}), ["none"]);
  assert.deepEqual(runRow("viewer", run({ costs: { CREDIT: "1.00000000", PROVIDER_USD: "2.00000000" } })).costs, ["1.00000000 CREDIT", "2.00000000 PROVIDER_USD"]);
});

test("B4-V06 an estimate with too few cases or clusters shows no difference, no interval and no improvement; no latency is not a number", () => {
  const base = REPORTS.reject;
  const thin: Report = { ...base, estimates: { ...base.estimates, slices: { safety: { n: 1, clusters: 1, diff: null, low: null, high: null, verdict: "insufficient", improved: false } } } };
  const safety = comparison(thin).estimates[1];
  assert.deepEqual([safety.name, safety.diff, safety.interval, safety.verdict, safety.improved], ["slice safety", "—", "—", "too few cases to decide", ""]);
  assert.equal(safety.paired, "1 paired · 1 clusters · needs 5");
  const quiet: Report = { ...base, observed: { ...base.observed, baseline: { ...base.observed.baseline, latency_ms: { n: 0 } } } };
  assert.equal(comparison(quiet).runs[0].latency, "no latencies");
});

const sub = (over: Partial<Subscription> = {}): Subscription => ({
  subscription_id: "s1", external_run_ref: "lab:external_run:x", dataset_ref: "lab:dataset:x", harness_ref: "lab:harness:x",
  evaluator_ref: "lab:evaluator:x", seed: 7, max_cases: 50, run_limit: { unit: "CREDIT", value: "5.00000000" },
  limit: { unit: "CREDIT", value: "20.00000000" }, max_active: 2, policy: "latest_only", decisions: [], ...over,
});

test("B4-V07 a subscription shows its pinned suite, CREDIT limits and one decision per checkpoint as recorded", () => {
  const row = subscriptionRow(sub({ decisions: [
    { checkpoint_id: "c1", step: 100, receipt: "evaluated", state: "queued", reason: null, run_id: "r9" },
    { checkpoint_id: "c0", step: 50, receipt: "validated", state: "skipped", reason: "superseded", run_id: null },
    { checkpoint_id: "c2", step: 150, receipt: "rejected", state: null, reason: null, run_id: null },
    { checkpoint_id: "c3", step: 200, receipt: "received", state: null, reason: null, run_id: null },
  ] }));
  assert.equal(row.budget, "5.00000000 CREDIT per run · 20.00000000 CREDIT in total · 2 at once");
  assert.equal(row.policy, "latest only: a checkpoint older than one already received is skipped as superseded");
  same(row.decisions.map((d) => [d.checkpoint, d.step, d.receipt, d.outcome]), [
    ["c1", "100", "evaluated", "evaluation queued · run r9"],
    ["c0", "50", "validated", "skipped · superseded"],
    ["c2", "150", "rejected", "not evaluated: the checkpoint was rejected"],
    ["c3", "200", "received", "not decided yet"],
  ]);
  assert.equal(subscriptionRow(sub({ policy: "every" })).policy, "every checkpoint, in any order");
});

test("B4-V08 a refusal is fixed copy for a known reason and nothing for anything else in the URL", () => {
  for (const [reason, copy] of Object.entries(REFUSAL_COPY)) assert.equal(refusalCopy(reason), copy);
  for (const junk of ["<script>", "toString", "", undefined, ["denied"]]) assert.equal(refusalCopy(junk), null);
});

test("B4-V09 the evaluation port fails closed: unavailable until the real adapter is wired, the preview never in production", async () => {
  const port = evaluationPort({});
  const actor = { providerId: "p", role: "administrator" as const };
  for (const read of [port.catalog(actor), port.runs(actor), port.experiments(actor), port.subscriptions(actor), port.cancel(actor, "r")])
    assert.deepEqual(await read, { ok: false, reason: "unavailable" });
  assert.equal(isPreview({ LAB_EVALS_PREVIEW: "1", NODE_ENV: "development" }), true);
  assert.equal(isPreview({ LAB_EVALS_PREVIEW: "1", NODE_ENV: "production" }), false);
  assert.equal(isPreview({ LAB_EVALS_PREVIEW: "true" }), false);
  assert.notDeepEqual(await evaluationPort({ LAB_EVALS_PREVIEW: "1" }).runs(actor), { ok: false, reason: "unavailable" });
});
