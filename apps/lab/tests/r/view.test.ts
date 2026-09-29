// R4: what a provider sees and may propose, derived only from the D9/R1/R2/R3 records (never button state).
import assert from "node:assert/strict";
import test from "node:test";
import { isPreview, releasesPort } from "../../lib/services/rollouts/port.ts";
import { REFUSAL_COPY, refusalCopy, releaseRows, variantRows } from "../../lib/services/rollouts/view.ts";
import { BASE, CAND, EXPAND, POLICY, proposal, records, ref, release, RUNS, SOURCES, T, variant } from "./fixtures.ts";

const row = (r = release(), role: "viewer" | "developer" | "administrator" = "administrator", rec = records([r])) =>
  releaseRows(role, rec).find((x) => x.id === r.policyRef)!;

test("R4-V01 a release row shows the frozen plan, cohort, baseline and candidate weights from the D9 record", () => {
  const r = row();
  assert.deepEqual([r.id, r.setup, r.baseline, r.candidates], [POLICY, "canary · account cohort · policy v1", BASE, [`${CAND} · 5.00%`]]);
  assert.equal(r.plan, "horizon 86400 s · at least 500 candidate requests · errors ≤ 2% · p99 ≤ 4000 ms · cohort skew ≤ 200 bp · quality coverage ≥ 50% · metrics lag ≤ 300 s · budget 50.00 PROVIDER_USD");
});

test("R4-V02 progress shows traffic, errors, p99, quality coverage, spend and assignments from R1's aggregates; empty metrics show dashes", () => {
  const r = row();
  assert.deepEqual([r.traffic, r.errors, r.p99, r.quality, r.spend], ["candidate 120 · baseline 2280 requests", "1.7%", "3100 ms", "60 of 120", "12.40 of 50.00 PROVIDER_USD"]);
  assert.deepEqual(r.assignments, [`${BASE}: 2280 (cohort)`, `${CAND}: 3 (explicit)`]);
  const none = row(release({ progress: null }));
  assert.deepEqual([none.traffic, none.errors, none.p99, none.quality, none.spend, none.assignments], ["no traffic observed", "—", "—", "—", "—", []]);
  const idle = row(release({ progress: { ...release().progress!, candidate: { requests: 0, errors: 0, p99Ms: null }, qualityCovered: 0 } }));
  assert.deepEqual([idle.errors, idle.p99, idle.quality], ["—", "—", "—"]);
});

test("R4-V03 spend in another unit than the budget is named, never converted", () => {
  const r = row(release({ progress: { ...release().progress!, spent: { amount: "900", unit: "CREDIT" } } }));
  assert.equal(r.spend, "spend in CREDIT against a PROVIDER_USD budget: not converted");
});

test("R4-V04 an inconclusive evaluation blocks promotion and says so; only an expand verdict offers expansion", () => {
  const held = row();
  assert.equal(held.status, "running · hold: min_requests, report_inconclusive");
  assert.equal(held.blocked, "Promotion blocked: the evaluation is inconclusive.");
  assert.deepEqual(held.actions, ["rollback"]);
  const noReport = row(release({ verdict: { action: "hold", reasons: ["no_report"], evidenceRefs: [], evaluatedAt: T } }));
  assert.equal(noReport.blocked, "Promotion blocked until every guardrail passes.");
  assert.deepEqual(noReport.actions, ["rollback"]);
  const fresh = row(release({ verdict: null }));
  assert.deepEqual([fresh.status, fresh.blocked, fresh.actions], ["running · not evaluated yet", "Promotion blocked until every guardrail passes.", ["rollback"]]);
  const ready = row(release({ verdict: EXPAND }));
  assert.deepEqual([ready.status, ready.blocked, ready.actions], ["running · expand: every guardrail passes", null, ["expand", "rollback"]]);
});

test("R4-V05 actions follow the record and the role: only an administrator proposes; nothing on a rolled-back release or while a request is pending", () => {
  const acts = (role: "viewer" | "developer" | "administrator", r = release({ verdict: EXPAND }), p = [] as ReturnType<typeof proposal>[]) =>
    row(r, role, records([r], [], p)).actions;
  assert.deepEqual(acts("viewer"), []);
  assert.deepEqual(acts("developer"), []);
  assert.deepEqual(acts("administrator"), ["expand", "rollback"]);
  assert.deepEqual(acts("administrator", release({ state: "approved", verdict: EXPAND })), ["rollback"]);
  assert.deepEqual(acts("administrator", release({ state: "rolled_back", verdict: EXPAND })), []);
  assert.deepEqual(acts("administrator", undefined, [proposal()]), []);
  assert.deepEqual(acts("administrator", undefined, [proposal({ state: "rejected" }), proposal({ policyRef: ref("policy", "a") })]), ["expand", "rollback"]);
  const r = release({ fence: 7 });
  const pending = row(r, "administrator", records([r], [], [proposal()]));
  assert.deepEqual([pending.pending, pending.fence], ["rollback proposed · awaiting operator approval", 7]);
});

test("R4-V06 a rollback is shown from the D9 decision records with reasons and who decided, never from a request", () => {
  const by = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const back = release({ state: "rolled_back" });
  const decisions = [
    { policyRef: POLICY, decision: "rollback" as const, reasons: ["error_rate", "latency"], evidenceRefs: [], decidedBy: by, decidedAt: T },
    { policyRef: ref("policy", "a"), decision: "expand" as const, reasons: [], evidenceRefs: RUNS, decidedBy: by, decidedAt: T },
  ];
  const r = row(back, "viewer", records([back], decisions));
  assert.equal(r.status, `rolled back · serving returns to ${BASE}`);
  assert.deepEqual([r.lineage, r.blocked], [[`rollback by ${by} at ${T}: error_rate, latency`], null]);
  const up = release({ state: "approved" });
  const approved = row(up, "viewer", records([up], [{ ...decisions[1], policyRef: POLICY }]));
  assert.equal(approved.status, "expansion approved by an operator");
  assert.deepEqual(approved.lineage, [`expand by ${by} at ${T} · evidence ${RUNS.join(", ")}`]);
  const asked = row(release(), "viewer", records([release()], [], [proposal()]));
  assert.deepEqual([asked.status, asked.lineage], ["running · hold: min_requests, report_inconclusive", []]);
});

test("R4-V07 a variant row names the hardware and runtime scope of both sides and what changed", () => {
  const [v] = variantRows([variant()]);
  assert.deepEqual([v.id, v.base, v.variant, v.changes], [ref("variant", "8"), "vllm 0.11.2 on B300 · bf16 · text", "vllm 0.11.2 on B300 · nvfp4 · text", "quantization:nvfp4"]);
});

test("R4-V12 a variant without its identities (R3 has not persisted them, R252) shows its serving refs", () => {
  const [v] = variantRows([variant({ base: null, variant: undefined })]);
  assert.deepEqual([v.base, v.variant], [variant().baseServingRef, variant().variantServingRef]);
});

test("R4-V08 performance is a measurement only when every source is an experiment results path at a commit", () => {
  const perf = (sources: string[] | null) =>
    variantRows([variant({}, { performance: sources && { ...variant().comparison!.performance!, sources } })])[0].performance;
  assert.equal(perf(SOURCES), `measured: throughput ×1.42 · p99 -120 ms · memory -12.0 GiB (${SOURCES.join(", ")})`);
  assert.equal(perf([SOURCES[0], "fixtures/perf.json"]), "synthetic fixture, not a measurement");
  assert.equal(perf([SOURCES[0], "marlin2b/results/load.json"]), "synthetic fixture, not a measurement");
  assert.equal(perf(null), "not measured");
});

test("R4-V09 an optimization is claimed and a variant is eligible only for an equivalent comparison", () => {
  const one = (c: Parameters<typeof variant>[1], over = {}) => variantRows([variant(over, c)])[0];
  const good = one({});
  assert.deepEqual([good.outcome, good.claim, good.eligible], ["equivalent", "optimization claimed for this scope only", "eligible as a release candidate"]);
  const unsure = one({ outcome: "inconclusive", reasons: ["capability_unverified:tools"], optimizationClaimed: false });
  assert.deepEqual([unsure.claim, unsure.eligible], ["no optimization claimed", "not eligible: inconclusive: capability_unverified:tools"]);
  assert.equal(one({ outcome: "rejected", reasons: ["slice code inferior"] }).claim, "no optimization claimed");
  assert.equal(one({ optimizationClaimed: false }).claim, "no optimization claimed");
  assert.equal(one({ performance: { ...variant().comparison!.performance!, sources: ["fixtures/perf.json", "x"] } }).claim, "no optimization claimed");
  const bare = variantRows([variant({ comparison: null })])[0];
  assert.deepEqual([bare.outcome, bare.performance, bare.claim, bare.eligible], ["not compared", "not measured", "no optimization claimed", "not eligible: not compared"]);
});

test("R4-V10 a refusal is fixed copy for a known reason and nothing for anything else in the URL", () => {
  assert.equal(refusalCopy("conflict"), REFUSAL_COPY.conflict);
  for (const bad of ["<script>", "constructor", "toString", undefined, ["denied"]]) assert.equal(refusalCopy(bad), null);
});

test("R4-V11 the releases port fails closed: unavailable until the real adapter is wired, the preview never in production", async () => {
  const actor = { providerId: "p", role: "administrator" as const };
  assert.deepEqual(await releasesPort({}).releases(actor), { ok: false, reason: "unavailable" });
  assert.deepEqual(await releasesPort({}).variants(actor), { ok: false, reason: "unavailable" });
  assert.deepEqual(await releasesPort({}).propose(actor, "rollback", POLICY, 1), { ok: false, reason: "unavailable" });
  assert.deepEqual(await releasesPort({ LAB_RELEASES_PREVIEW: "1", NODE_ENV: "production" }).releases(actor), { ok: false, reason: "unavailable" });
  assert.equal(isPreview({ LAB_RELEASES_PREVIEW: "true" }), false);
  assert.equal(isPreview({ LAB_RELEASES_PREVIEW: "1" }), true);
  assert.deepEqual(await releasesPort({ LAB_RELEASES_PREVIEW: "1" }).releases(actor), { ok: true, value: records([]) });
});
