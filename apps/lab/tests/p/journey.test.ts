// P4 journey over the LAB_PIPELINES stand-in (the WR-P4-1 shape; the P1/P3 routes are not on the base):
// label import → correction → adjudication → train-only export → manual training bundle → checkpoint →
// held-out evaluation → eligible candidate, and the variants that must not get through.
import assert from "node:assert/strict";
import test from "node:test";
import { FakePipelines } from "../../lib/services/pipelines/fake.ts";
import type { Actor, Result } from "../../lib/services/pipelines/port.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const ref = (kind: string, provider: string, id: string) => `lab:${kind}:${provider}:${id}@sha256:${"a".repeat(64)}`;
const uuid = (n: number) => `${String(n).padStart(8, "0")}-0000-4000-8000-000000000000`;
const DATASET = ref("dataset", A, uuid(1));
const RUBRIC = ref("rubric", A, uuid(2));
const PAYER = ref("payer", A, uuid(3));
const dev: Actor = { providerId: A, role: "developer" };
const admin: Actor = { providerId: A, role: "administrator" };
const viewer: Actor = { providerId: A, role: "viewer" };
const other: Actor = { providerId: B, role: "administrator" };
const value = <T>(r: Result<T>): T => {
  assert.ok(r.ok, `refused: ${JSON.stringify(r)}`);
  return r.value;
};
const reason = (r: Result<unknown>) => (r.ok ? "ok" : r.reason);
/** Compared as JSON text: a failure shows both values whole (no elided deep-equal diff). */
const same = (actual: unknown, expected: unknown, message?: string) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const rows = (...lines: object[]) => lines.map((l) => JSON.stringify(l)).join("\n");

function world() {
  const lab = new FakePipelines();
  lab.dataset(A, DATASET, [
    { sampleId: "s1", split: "train" }, { sampleId: "s2", split: "train" }, { sampleId: "s3", split: "validation" },
    { sampleId: "s4", split: "holdout" }, { sampleId: "s5", split: "train", readable: false },
  ]);
  return lab;
}
const CONFIG = { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" } as const;

async function trained(lab: FakePipelines) {
  value(await lab.importLabels(dev, { importId: uuid(10), datasetRef: DATASET, rubricRef: RUBRIC, rows: rows(
    { sample_id: "s1", method: "model", method_version: "m1", label: "yes" },
    { sample_id: "s2", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s4", method: "human", method_version: "h1", label: "no" },
  ) }));
  const labels = value(await lab.labels(dev, DATASET));
  for (const l of labels) value(await lab.review(dev, { datasetRef: DATASET, annotationRef: l.annotationRef, decision: "accepted", rubricRef: RUBRIC, correction: null }));
  const exported = value(await lab.exportLabels(dev, { exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 }));
  const run = value(await lab.prepare(dev, { externalRunId: uuid(30), datasetRef: DATASET, exportFormat: "infrx.label_export.1", exportId: exported.exportId, config: CONFIG, payerRef: PAYER, limitUsd: "25.00000000" }));
  return { exported, run };
}

test("P4-J01 import → correction → adjudication → train-only export: methods kept apart, lineage and omissions recorded", async () => {
  const lab = world();
  const receipt = value(await lab.importLabels(dev, { importId: uuid(10), datasetRef: DATASET, rubricRef: RUBRIC, rows: rows(
    { sample_id: "s1", method: "model", method_version: "m1", label: "yes" },
    { sample_id: "s1", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s2", method: "human", method_version: "h1", label: "no", ground_truth: true },
    { sample_id: "nope", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s5", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s2", method: "annotator", method_version: "h1", label: "no" },
  ) }));
  assert.equal(receipt.accepted, 2);
  same(receipt.rejected, [
    { row: 3, reason: "forged_ground_truth" }, { row: 4, reason: "missing_evidence" }, { row: 5, reason: "grant_not_current" }, { row: 6, reason: "bad_mapping" },
  ]);
  same(value(await lab.imports(dev)).map((r) => r.importId), [uuid(10)]);
  const first = { importId: uuid(10), datasetRef: DATASET, rubricRef: RUBRIC, rows: rows(
    { sample_id: "s1", method: "model", method_version: "m1", label: "yes" },
    { sample_id: "s1", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s2", method: "human", method_version: "h1", label: "no", ground_truth: true },
    { sample_id: "nope", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s5", method: "human", method_version: "h1", label: "no" },
    { sample_id: "s2", method: "annotator", method_version: "h1", label: "no" },
  ) };
  same(value(await lab.importLabels(dev, first)), receipt, "a replay is the stored receipt");
  assert.equal(reason(await lab.importLabels(dev, { ...first, rows: "" })), "conflict");
  assert.equal(value(await lab.labels(dev, DATASET)).length, 2);
  let labels = value(await lab.labels(dev, DATASET));
  same(labels.map((l) => [l.method, l.groundTruth, l.state]), [["synthetic", false, "submitted"], ["imported", false, "submitted"]]);
  same(value(await lab.disagreements(dev, DATASET)), [{ sampleId: "s1", annotationRefs: labels.map((l) => l.annotationRef).sort() }]);

  value(await lab.assign(admin, { datasetRef: DATASET, sampleId: "s1", reviewerId: uuid(99), rubricRef: RUBRIC }));
  value(await lab.review(dev, { datasetRef: DATASET, annotationRef: labels[0].annotationRef, decision: "rejected", rubricRef: RUBRIC, correction: '"no"' }));
  labels = value(await lab.labels(dev, DATASET));
  same(labels.map((l) => [l.method, l.groundTruth, l.state, l.value]), [
    ["synthetic", false, "rejected", '"yes"'], ["imported", false, "submitted", '"no"'], ["human", true, "accepted", '"no"'],
  ]);
  same(value(await lab.disagreements(dev, DATASET)), []);
  assert.equal(reason(await lab.review(dev, { datasetRef: DATASET, annotationRef: labels[0].annotationRef, decision: "accepted", rubricRef: RUBRIC, correction: null })), "conflict");
  assert.equal(reason(await lab.adjudicate(dev, { datasetRef: DATASET, sampleId: "s1", value: '"no"', rubricRef: RUBRIC })), "conflict");

  value(await lab.importLabels(dev, { importId: uuid(11), datasetRef: DATASET, rubricRef: RUBRIC, rows: rows(
    { sample_id: "s2", method: "model", method_version: "m1", label: "a" }, { sample_id: "s2", method: "human", method_version: "h1", label: "b" },
    { sample_id: "s3", method: "human", method_version: "h1", label: "c" }, { sample_id: "s4", method: "human", method_version: "h1", label: "d" },
  ) }));
  value(await lab.adjudicate(dev, { datasetRef: DATASET, sampleId: "s2", value: '"b"', rubricRef: RUBRIC }));
  labels = value(await lab.labels(dev, DATASET));
  same(labels.filter((l) => l.sampleId === "s2").map((l) => [l.method, l.state]), [["synthetic", "rejected"], ["imported", "rejected"], ["human", "accepted"]]);
  for (const l of labels.filter((x) => x.state === "submitted"))
    value(await lab.review(dev, { datasetRef: DATASET, annotationRef: l.annotationRef, decision: "accepted", rubricRef: RUBRIC, correction: null }));

  const exported = value(await lab.exportLabels(dev, { exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 }));
  assert.equal(exported.items, 2);
  same(exported.lineage.map((x) => [x.sampleId, x.methods]), [["s1", ["human", "imported"]], ["s2", ["human"]]]);
  same(exported.omitted, [{ sampleId: "s3", reason: "validation" }, { sampleId: "s4", reason: "holdout" }]);
  same(value(await lab.exportLabels(dev, { exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 })), exported);
  assert.equal(reason(await lab.exportLabels(dev, { exportId: uuid(20), datasetRef: DATASET, adapter: "preference.1", ttlS: 3600 })), "conflict");
  value(await lab.importLabels(dev, { importId: uuid(12), datasetRef: DATASET, rubricRef: RUBRIC, rows: rows({ sample_id: "s1", method: "human", method_version: "h2", label: "maybe" }) }));
  const late = value(await lab.labels(dev, DATASET)).at(-1)!;
  value(await lab.review(dev, { datasetRef: DATASET, annotationRef: late.annotationRef, decision: "accepted", rubricRef: RUBRIC, correction: null }));
  const split = value(await lab.exportLabels(dev, { exportId: uuid(21), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 }));
  same([split.items, split.omitted[0]], [1, { sampleId: "s1", reason: "disagreement" }]);
});

test("P4-J02 manual bundle → submit (nothing reserved) → finish → checkpoint → held-out evaluation → eligible, all replay-safe", async () => {
  const lab = world();
  const { exported, run } = await trained(lab);
  same([run.state, run.connector, run.limitUsd, run.payerRef, run.reservedUsd], ["prepared", "manual-bundle", "25.00000000", PAYER, "0.00000000"]);
  same([run.train, run.dev, run.omitted, run.holdout.size, run.export.exportId], [2, 1, 1, 1, exported.exportId]);
  const input = { externalRunId: uuid(30), datasetRef: DATASET, exportFormat: "infrx.label_export.1", exportId: exported.exportId, config: CONFIG, payerRef: PAYER, limitUsd: "25.00000000" };
  same(value(await lab.prepare(dev, input)), run);
  assert.equal(reason(await lab.prepare(dev, { ...input, limitUsd: "99.00000000" })), "conflict");
  assert.equal(value(await lab.runs(dev)).length, 1);
  const bundle = JSON.parse(value(await lab.bundle(dev, uuid(30))));
  same([bundle.external_run_ref, bundle.train, bundle.dev, bundle.holdout], [run.runRef, ["s1", "s2"], ["s3"], run.holdout]);

  assert.equal(value(await lab.submit(dev, uuid(30))).state, "submitted");
  assert.equal(value(await lab.submit(dev, uuid(30))).state, "submitted");
  assert.equal(lab.reservations, 0);
  assert.equal(value(await lab.finish(dev, uuid(30))).state, "completed");
  assert.equal(value(await lab.finish(dev, uuid(30))).state, "completed");

  const key = `lab/${A}/training/${uuid(30)}/adapter.json`;
  lab.artifact(key, `sha256:${"c".repeat(64)}`);
  const good = { externalRunId: uuid(30), checkpointId: uuid(40), artifactKey: key, artifactDigest: `sha256:${"c".repeat(64)}` };
  assert.equal(reason(await lab.approve(dev, { externalRunId: uuid(30), checkpointId: uuid(40) })), "conflict");
  const ckpt = value(await lab.importCheckpoint(dev, good));
  same([ckpt.state, ckpt.evaluation?.state, ckpt.evaluation?.split, ckpt.evaluation?.holdoutSha256], ["validated", "queued", "holdout", run.holdout.sha256]);
  same(value(await lab.importCheckpoint(dev, good)), ckpt);
  assert.equal(lab.evaluationsQueued, 1);
  assert.equal(reason(await lab.approve(dev, { externalRunId: uuid(30), checkpointId: uuid(40) })), "conflict");
  lab.evaluated(uuid(40), "succeeded");
  const eligible = value(await lab.approve(dev, { externalRunId: uuid(30), checkpointId: uuid(40) }));
  assert.equal(eligible.eligible, true);
  same(value(await lab.checkpoints(dev)).map((c) => [c.checkpointId, c.eligible]), [[uuid(40), true]]);
});

test("P4-J03 a bad, late or mismatched checkpoint is rejected with its reason and never evaluated; a redelivery keeps the first outcome", async () => {
  const lab = world();
  await trained(lab);
  const key = (n: string) => `lab/${A}/training/${uuid(30)}/${n}`;
  const digest = `sha256:${"c".repeat(64)}`;
  lab.artifact(key("a"), digest);
  const early = value(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(41), artifactKey: key("a"), artifactDigest: digest }));
  same([early.state, early.reason, early.evaluation], ["rejected", "run_prepared", null]);
  value(await lab.submit(dev, uuid(30)));
  assert.equal(value(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(41), artifactKey: key("a"), artifactDigest: digest })).reason, "run_prepared");
  const missing = value(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(42), artifactKey: key("gone"), artifactDigest: digest }));
  assert.equal(missing.reason, "missing_artifact");
  const mismatch = value(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(43), artifactKey: key("a"), artifactDigest: `sha256:${"d".repeat(64)}` }));
  assert.equal(mismatch.reason, "digest_mismatch");
  assert.equal(reason(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(44), artifactKey: `lab/${A}/training/${uuid(31)}/a`, artifactDigest: digest })), "invalid");
  value(await lab.cancel(dev, uuid(30)));
  const late = value(await lab.importCheckpoint(dev, { externalRunId: uuid(30), checkpointId: uuid(45), artifactKey: key("a"), artifactDigest: digest }));
  assert.equal(late.reason, "run_cancelled");
  assert.equal(lab.evaluationsQueued, 0);
  lab.evaluated(uuid(45), "succeeded");
  assert.equal(reason(await lab.approve(dev, { externalRunId: uuid(30), checkpointId: uuid(45) })), "conflict");
});

test("P4-J04 an ambiguous submission is only ever looked up, never resubmitted; a revoked grant stops a submit", async () => {
  const lab = world();
  await trained(lab);
  lab.ambiguous(uuid(30));
  assert.equal(value(await lab.submit(dev, uuid(30))).state, "ambiguous");
  assert.equal(value(await lab.submit(dev, uuid(30))).state, "ambiguous");
  same([lab.submissions, lab.lookups], [0, 2]);
  assert.equal(reason(await lab.cancel(dev, uuid(30))), "conflict");
  lab.found(uuid(30));
  assert.equal(value(await lab.submit(dev, uuid(30))).state, "submitted");
  assert.equal(lab.submissions, 0);

  const again = world();
  await trained(again);
  again.revoke(DATASET, "s1");
  assert.equal(reason(await again.submit(dev, uuid(30))), "denied");
  assert.equal(value(await again.runs(dev))[0].state, "prepared");
});

test("P4-J05 unauthorized variants: another provider, a viewer, a developer assigning, a foreign payer, an expired export", async () => {
  const lab = world();
  const { exported } = await trained(lab);
  for (const r of [await lab.labels(other, DATASET), await lab.submit(other, uuid(30)), await lab.bundle(other, uuid(30)),
    await lab.importLabels(other, { importId: uuid(12), datasetRef: DATASET, rubricRef: RUBRIC, rows: "" })])
    assert.equal(reason(r), "not_found");
  same(value(await lab.runs(other)), []);
  same(value(await lab.imports(other)), []);
  same(value(await lab.exports(other)), []);
  same(value(await lab.checkpoints(other)), []);
  for (const r of [await lab.labels(viewer, DATASET), await lab.runs(viewer), await lab.submit(viewer, uuid(30))]) assert.equal(reason(r), "denied");
  assert.equal(reason(await lab.assign(dev, { datasetRef: DATASET, sampleId: "s1", reviewerId: uuid(99), rubricRef: RUBRIC })), "denied");
  const input = { datasetRef: DATASET, exportFormat: "infrx.label_export.1", exportId: exported.exportId, config: CONFIG, limitUsd: "1.00000000" };
  assert.equal(reason(await lab.prepare(dev, { ...input, externalRunId: uuid(31), payerRef: ref("payer", B, uuid(3)) })), "invalid");
  lab.expire(exported.exportId);
  assert.equal(reason(await lab.prepare(dev, { ...input, externalRunId: uuid(32), payerRef: PAYER })), "gone");
  assert.equal(value(await lab.runs(dev)).length, 1);
});

test("P4-J06 teacher batch: a dry run sends nothing; only an administrator approves within the budget; a double click is one batch; ambiguous, stopped and unauthorized variants", async () => {
  const lab = world();
  const input = { batchId: uuid(50), datasetRef: DATASET, rubricRef: RUBRIC, teacherModel: "claude-opus-5", promptVersion: "teach-v1", payerRef: PAYER, budgetUsd: "1.00000000", chunkSize: 2 };
  const planned = value(await lab.planTeachers(dev, input));
  same([planned.ceilingUsd, planned.withinBudget, planned.holdout, planned.notPermitted, planned.approval, planned.chunks.map((c) => [c.samples, c.ceilingUsd, c.state])],
    ["0.45416000", true, 1, 1, null, [[2, "0.22708000", "unreserved"], [2, "0.22708000", "unreserved"]]]);
  assert.equal(lab.teacherSends, 0, "a dry run sends nothing");
  same(value(await lab.planTeachers(admin, input)), planned, "the same batch id again is the stored batch");
  assert.equal(reason(await lab.planTeachers(dev, { ...input, chunkSize: 1 })), "conflict");
  same(value(await lab.teacherBatches(dev)), [planned]);
  assert.equal(reason(await lab.approveTeachers(dev, uuid(50))), "denied");
  assert.equal(reason(await lab.approveTeachers(viewer, uuid(59))), "not_found", "the batch before the role (R183)");
  assert.equal(reason(await lab.approveTeachers(other, uuid(50))), "not_found");
  value(await lab.planTeachers(dev, { ...input, batchId: uuid(51), budgetUsd: "0.40000000" }));
  value(await lab.planTeachers(dev, { ...input, batchId: uuid(52), teacherModel: "unpriced" }));
  for (const id of [uuid(51), uuid(52)]) assert.equal(reason(await lab.approveTeachers(admin, id)), "conflict", id);
  lab.teacherLive = false;
  assert.equal(reason(await lab.approveTeachers(admin, uuid(50))), "unavailable");
  lab.teacherLive = true;
  assert.equal(lab.teacherSends, 0);
  const approved = value(await lab.approveTeachers(admin, uuid(50)));
  same([approved.approval?.approvedBy, approved.chunks.map((c) => [c.state, c.reservedUsd, c.sent])], ["session-user", [["submitted", "0.22708000", 2], ["submitted", "0.22708000", 1]]]);
  same(value(await lab.approveTeachers(admin, uuid(50))), approved, "a double click resumes and sends nothing more");
  assert.equal(lab.teacherSends, 2);
  for (const bad of [{ payerRef: ref("payer", B, uuid(3)) }, { datasetRef: ref("dataset", B, uuid(1)) }, { budgetUsd: "1" }, { chunkSize: 201 }])
    assert.equal(reason(await lab.planTeachers(dev, { ...input, batchId: uuid(53), ...bad })), "invalid", JSON.stringify(bad));
  assert.equal(reason(await lab.planTeachers(viewer, { ...input, batchId: uuid(53) })), "denied");
  same(value(await lab.teacherBatches(other)), []);

  const lost = world();
  lost.teacherMode = "lost";
  lost.payerBudgetUsd = "0.30000000";
  value(await lost.planTeachers(dev, input));
  const held = value(await lost.approveTeachers(admin, uuid(50)));
  same(held.chunks.map((c) => [c.state, c.reservedUsd]), [["ambiguous", "0.22708000"], ["unreserved", null]], "the payer's budget stops the batch; the lost answer is held");
  assert.equal(lost.teacherSends, 1);
  value(await lost.approveTeachers(admin, uuid(50)));
  assert.equal(lost.teacherSends, 1, "an ambiguous chunk is never resent");
  lost.teacherFailures(held.chunks[0].runId, [{ sampleId: "s1", reason: "malformed_label" }]);
  same(value(await lost.teacherBatches(dev))[0].chunks[0].failures, [{ sampleId: "s1", reason: "malformed_label" }]);
});
