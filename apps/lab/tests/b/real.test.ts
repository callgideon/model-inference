// B4 against the merged backends' own output (real/dump.py on the b3 PostgreSQL): D7's run status
// as B1 leaves it and B2's reports render with the counts, slices, uncertainty and units they carry.
import assert from "node:assert/strict";
import test from "node:test";
import { comparison, runRow } from "../../lib/services/evaluation/view.ts";
import { REPORTS, RUNS, same } from "./real.ts";

test("B4-R01 real D7 run records: a cancelled run's in-flight case stays unfinished, a queued one waits", () => {
  const cancelled = runRow("developer", RUNS.cancelled);
  assert.deepEqual([cancelled.state, cancelled.done, cancelled.open, cancelled.costs, cancelled.cancel],
    ["cancelled", "1 / 4 done", "3 / 4 not finished", ["1.56000000 CREDIT"], false]);
  const queued = runRow("developer", RUNS.queued);
  assert.deepEqual([queued.state, queued.open, queued.attempts, queued.costs, queued.cancel],
    ["queued · waiting for a worker", "4 / 4 not finished", "none yet", ["none"], true]);
  assert.deepEqual([runRow("viewer", RUNS.succeeded).done, runRow("viewer", RUNS.succeeded).failed], ["4 / 4 done", "0 / 4 failed"]);
});

test("B4-C01 a required slice that is inferior rejects the candidate even when the overall estimate improved", () => {
  const c = comparison(REPORTS.reject);
  assert.deepEqual([c.outcome, c.reasons], ["Reject: a predeclared criterion failed", ["slice safety inferior"]]);
  const [overall, safety] = c.estimates;
  assert.deepEqual([overall.name, overall.verdict, overall.improved, overall.margin], ["overall", "non-inferior", "improved", "−0.05"]);
  assert.deepEqual([safety.name, safety.verdict, safety.improved, safety.margin, safety.diff], ["slice safety", "inferior", "", "−0", "-0.8"]);
  assert.equal(safety.interval, "[-1.3538746213681456, -0.24612537863185446]");
  assert.equal(overall.paired, "40 paired · 40 clusters · needs 10");
  assert.deepEqual([c.basis, c.confidence, c.pairing], ["deterministic metric", "95% intervals", "single factor: serving_ref"]);
});

test("B4-C02 missing, errored and unscored cases stay in view and a coverage gap is inconclusive, never accepted", () => {
  const c = comparison(REPORTS.inconclusive);
  assert.deepEqual([c.outcome, c.reasons], ["Inconclusive: the evidence does not decide this comparison", ["coverage 38/40", "overall uncertain"]]);
  assert.equal(c.paired, "38 / 40 cases paired");
  const [base, cand] = c.runs;
  assert.deepEqual([cand.name, cand.mean, cand.missing, cand.errors, cand.notComparable], ["candidate", "0.925", "1 / 40 missing", "1 errored", "1 not comparable"]);
  assert.deepEqual([base.name, base.mean, base.missing], ["baseline", "1", "0 / 40 missing"]);
  assert.equal(cand.latency, "p50 119 · p90 135 · p99 138 · max 138 ms over 39");
  same(c.estimates.map((e) => [e.name, e.verdict, e.improved]), [["overall", "uncertain", ""], ["slice safety", "non-inferior", ""]]);
});

test("B4-C03 costs stay per unit: PROVIDER_USD beside CREDIT, and a delta only where both runs carry the unit", () => {
  const c = comparison(REPORTS.inconclusive);
  assert.deepEqual(c.runs[1].costs, ["39.00000000 CREDIT", "0.25000000 PROVIDER_USD"]);
  assert.deepEqual(c.runs[0].costs, ["40.00000000 CREDIT"]);
  assert.deepEqual(c.costDelta, ["-1.00000000 CREDIT"]);
});

test("B4-C04 the comparison names both runs, the case universe and the protocol by digest so it can be reproduced", () => {
  const r = REPORTS.reject;
  same(comparison(r).identity, [
    ["Report", r.report_digest], ["Baseline run", r.baseline_run], ["Candidate run", r.candidate_run],
    ["Case universe", r.universe_digest], ["Protocol", r.protocol_digest],
  ]);
});
