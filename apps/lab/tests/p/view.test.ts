// P4 view: every row and word derives from the P1/P3 records. Synthetic, imported and reviewed labels
// never read alike; an unknown submission, an unknown cost and a rejected checkpoint say so; a paid
// row shows its USD budget and payer; nothing offers a resubmit or an approval the records do not allow.
import assert from "node:assert/strict";
import test from "node:test";
import type { Checkpoint, Label, TeacherBatch, TrainingRun } from "../../lib/services/pipelines/port.ts";
import { holds, pipelinesPort } from "../../lib/services/pipelines/port.ts";
import {
  checkpointRows, exportRows, importRows, labelRows, refusalCopy, REFUSAL_COPY, runRows, teacherRows,
} from "../../lib/services/pipelines/view.ts";

const P = "11111111-1111-4111-8111-111111111111";
const ref = (kind: string, id: string) => `lab:${kind}:${P}:${id}@sha256:${"a".repeat(64)}`;
const DATASET = ref("dataset", "33333333-3333-4333-8333-333333333333");
const label = (over: Partial<Label>): Label => ({
  annotationRef: ref("annotation", "44444444-4444-4444-8444-444444444444"), sampleId: "s1", method: "synthetic",
  groundTruth: false, state: "submitted", value: '"yes"', reviewerId: null, ...over,
});
const RUN_ID = "55555555-5555-4555-8555-555555555555";
const run = (over: Partial<TrainingRun>): TrainingRun => ({
  externalRunId: RUN_ID, runRef: ref("external_run", RUN_ID), connector: "manual-bundle", state: "prepared",
  datasetRef: DATASET, export: { format: "infrx.label_export.1", exportId: "66666666-6666-4666-8666-666666666666", sha256: "e".repeat(64) },
  config: { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }, train: 2, dev: 1, omitted: 1,
  holdout: { size: 3, sha256: "f".repeat(64) }, payerRef: ref("payer", "77777777-7777-4777-8777-777777777777"),
  limitUsd: "25.00000000", reservedUsd: "0.00000000", settled: false, costUsd: null, reason: null, ...over,
});
const CKPT = "88888888-8888-4888-8888-888888888888";
const checkpoint = (over: Partial<Checkpoint>): Checkpoint => ({
  checkpointId: CKPT, externalRunId: RUN_ID, artifactDigest: `sha256:${"c".repeat(64)}`, state: "validated", reason: null,
  evaluation: { runRef: ref("run", "99999999-9999-4999-8999-999999999999"), state: "succeeded", split: "holdout", holdoutSha256: "f".repeat(64) },
  eligible: false, ...over,
});

test("P4-V01 roles mirror ROLE_CAPABILITIES: a viewer runs no pipeline, a developer does, only an administrator assigns", () => {
  assert.equal(holds("viewer", "run_evaluation"), false);
  assert.equal(holds("developer", "run_evaluation"), true);
  assert.equal(holds("developer", "manage_members"), false);
  assert.equal(holds("administrator", "manage_members"), true);
});

test("P4-V02 synthetic, imported and reviewed labels never read alike; only a human review is ground truth", () => {
  const [synthetic, imported, human, forged] = labelRows("developer", [
    label({ method: "synthetic" }), label({ method: "imported" }), label({ method: "human", groundTruth: true, state: "accepted", reviewerId: "u1" }),
    label({ method: "synthetic", groundTruth: true }),
  ]);
  assert.equal(new Set([synthetic.kind, imported.kind, human.kind]).size, 3);
  assert.match(synthetic.kind, /synthetic/i);
  assert.match(imported.kind, /imported/i);
  for (const row of [synthetic, imported, forged]) assert.equal(row.truth, "not ground truth");
  assert.equal(human.truth, "ground truth (human review)");
  assert.equal(human.reviewer, "u1");
});

test("P4-V03 only a submitted label can be reviewed, and only by a role that runs pipelines", () => {
  const rows = labelRows("developer", [label({}), label({ state: "accepted" }), label({ state: "rejected" }), label({ state: "superseded" })]);
  assert.deepEqual(rows.map((r) => r.reviewable), [true, false, false, false]);
  assert.equal(labelRows("viewer", [label({})])[0].reviewable, false);
});

test("P4-V04 an import receipt lists every rejected row with its reason; an unknown reason is not echoed", () => {
  const [row] = importRows([{ importId: "i1", datasetRef: DATASET, accepted: 2, rejected: [
    { row: 3, reason: "forged_ground_truth" }, { row: 4, reason: "grant_not_current" }, { row: 5, reason: "<script>" as never },
  ] }]);
  assert.equal(row.summary, "2 accepted · 3 rejected");
  assert.match(row.rejected[0], /^Row 3: .*ground truth/);
  assert.match(row.rejected[1], /^Row 4: .*grant/);
  assert.equal(row.rejected[2], "Row 5: rejected (reason not recognised)");
});

test("P4-V05 an export shows its lineage (sample → labels and methods) and every omitted sample with its reason", () => {
  const [row] = exportRows([{ exportId: "e1", datasetRef: DATASET, adapter: "sft.1", items: 1, expiresAt: "2026-10-01T00:00:00Z",
    lineage: [{ sampleId: "s1", labelRefs: ["l1", "l2"], methods: ["human", "synthetic"] }],
    omitted: [{ sampleId: "s2", reason: "holdout" }, { sampleId: "s3", reason: "disagreement" }] }]);
  assert.deepEqual(row.lineage, ["s1 ← l1, l2 (human, synthetic)"]);
  assert.deepEqual(row.omitted, ["s2: holdout", "s3: disagreement"]);
  assert.equal(row.expires, "2026-10-01T00:00:00Z");
});

test("P4-V06 a training run shows its USD budget and named payer, config, data and holdout pin", () => {
  const [row] = runRows("developer", [run({})]);
  assert.equal(row.budget, "limit 25.00000000 USD · reserved 0.00000000 USD");
  assert.equal(row.payer, ref("payer", "77777777-7777-4777-8777-777777777777"));
  assert.equal(row.config, "sft · lora · base marlin-2b");
  assert.equal(row.data, `${DATASET} · 2 train, 1 dev, 1 omitted · export 66666666-6666-4666-8666-666666666666`);
  assert.equal(row.holdout, `3 held-out samples, pinned ${"f".repeat(64)}`);
  assert.doesNotMatch(JSON.stringify(row), /CREDIT/);
});

test("P4-V07 an ambiguous submission reads as unknown with its hold, and offers no resubmit or cancel", () => {
  const [row] = runRows("administrator", [run({ state: "ambiguous", connector: "vendor-x", reservedUsd: "25.00000000" })]);
  assert.match(row.state, /outcome unknown/i);
  assert.match(row.state, /25\.00000000 USD held/);
  assert.match(row.state, /never resubmitted/);
  assert.deepEqual(row.actions, []);
});

test("P4-V08 cost is unsettled, unknown or as reported, never estimated; the manual bundle holds nothing", () => {
  const cost = (over: Partial<TrainingRun>) => runRows("developer", [run(over)])[0].cost;
  assert.match(cost({}), /nothing reserved/i);
  assert.equal(cost({ connector: "vendor-x" }), "Not settled yet.");
  assert.equal(cost({ connector: "vendor-x", settled: true, costUsd: null }), "Unknown: the provider reported no cost. It is never estimated.");
  assert.equal(cost({ connector: "vendor-x", settled: true, costUsd: "3.50000000" }), "3.50000000 USD, as reported.");
});

test("P4-V09 run actions follow the record: submit a prepared bundle, finish a manual one, cancel live ones; none for a viewer", () => {
  const acts = (over: Partial<TrainingRun>, role: "developer" | "viewer" = "developer") => runRows(role, [run(over)])[0].actions;
  assert.deepEqual(acts({}), ["submit", "cancel"]);
  assert.deepEqual(acts({ state: "submitted" }), ["finish", "cancel"]);
  assert.deepEqual(acts({ state: "submitted", connector: "vendor-x" }), ["cancel"]);
  for (const state of ["submitting", "completed", "failed", "cancelled"] as const) assert.deepEqual(acts({ state }), [], state);
  assert.deepEqual(acts({}, "viewer"), []);
  assert.match(runRows("developer", [run({ state: "failed", reason: "402: over budget" })])[0].state, /Failed: 402: over budget/);
});

test("P4-V10 a rejected checkpoint shows its reason and is never evaluated or approvable", () => {
  const rows = checkpointRows("developer", [
    checkpoint({ state: "rejected", reason: "digest_mismatch", evaluation: null }),
    checkpoint({ state: "rejected", reason: "run_cancelled", evaluation: null }),
    checkpoint({ state: "rejected", reason: "<b>x</b>", evaluation: null }),
  ], [run({})]);
  assert.match(rows[0].state, /^Rejected: .*digest/);
  assert.match(rows[1].state, /^Rejected: .*cancelled/);
  assert.equal(rows[2].state, "Rejected: reason not recognised.");
  for (const r of rows) {
    assert.equal(r.approvable, false);
    assert.equal(r.comparison, null);
  }
});

test("P4-V11 lineage runs from the checkpoint to its run, data, export, config and holdout pin", () => {
  const [row] = checkpointRows("developer", [checkpoint({})], [run({})]);
  assert.equal(row.lineage, `${ref("external_run", RUN_ID)} → ${DATASET} · export 66666666-6666-4666-8666-666666666666 · sft · lora · base marlin-2b · holdout ${"f".repeat(64)}`);
  const [orphan] = checkpointRows("developer", [checkpoint({ externalRunId: "other" })], [run({})]);
  assert.equal(orphan.lineage, "run other is not in this workspace's records");
  assert.equal(orphan.approvable, false);
});

test("P4-V12 approval needs a succeeded evaluation on exactly the run's pinned holdout; training metrics never promote", () => {
  const ok = (over: Partial<Checkpoint>, role: "developer" | "viewer" = "developer") => checkpointRows(role, [checkpoint(over)], [run({})])[0].approvable;
  const evaluation = checkpoint({}).evaluation!;
  assert.equal(ok({}), true);
  assert.equal(ok({}, "viewer"), false);
  assert.equal(ok({ eligible: true }), false);
  assert.equal(ok({ evaluation: { ...evaluation, state: "running" } }), false);
  assert.equal(ok({ evaluation: { ...evaluation, split: "validation" } }), false);
  assert.equal(ok({ evaluation: { ...evaluation, holdoutSha256: "0".repeat(64) } }), false);
  const [row] = checkpointRows("developer", [checkpoint({})], [run({})]);
  assert.equal(row.comparison, `/evaluations?run=${encodeURIComponent(evaluation.runRef)}`);
  assert.match(row.state, /held-out evaluation succeeded/i);
  assert.match(checkpointRows("developer", [checkpoint({ eligible: true })], [run({})])[0].state, /eligible candidate.*not public/i);
});

test("P4-V13 a refusal is fixed copy for a known reason and nothing for anything else in the URL", () => {
  assert.equal(refusalCopy("gone"), REFUSAL_COPY.gone);
  assert.equal(refusalCopy("conflict"), REFUSAL_COPY.conflict);
  for (const junk of ["toString", "<script>", undefined, ["denied"]]) assert.equal(refusalCopy(junk), null);
});

test("P4-V14 the pipelines port fails closed: unavailable until the real adapter is wired, the preview never in production", async () => {
  const actor = { providerId: P, role: "administrator" } as const;
  for (const env of [{}, { LAB_PIPELINES_PREVIEW: "1", NODE_ENV: "production" }, { LAB_PIPELINES_PREVIEW: "true" }])
    assert.deepEqual(await pipelinesPort(env).runs(actor), { ok: false, reason: "unavailable" }, JSON.stringify(env));
  assert.deepEqual(await pipelinesPort({ LAB_PIPELINES_PREVIEW: "1" }).runs(actor), { ok: true, value: [] });
});

const BATCH = "abababab-abab-4bab-8bab-abababababab";
const chunk = (over: Partial<TeacherBatch["chunks"][number]>): TeacherBatch["chunks"][number] => ({
  runId: "r1", samples: 4, ceilingUsd: "0.45416000", state: "unreserved", reservedUsd: null, costUsd: null, sent: 0, failures: [], ...over,
});
const batch = (over: Partial<TeacherBatch>): TeacherBatch => ({
  batchId: BATCH, datasetRef: DATASET, rubricRef: ref("rubric", "12121212-1212-4212-8212-121212121212"), teacherModel: "claude-opus-5",
  promptVersion: "teach-v1", payerRef: ref("payer", "77777777-7777-4777-8777-777777777777"), budgetUsd: "1.00000000", chunkSize: 4,
  requestedBy: "u-dev", priceVersion: "rates-v1", ceilingUsd: "0.68124000", withinBudget: true, holdout: 2, notPermitted: 1, approval: null,
  chunks: [chunk({}), chunk({ runId: "r2", samples: 2, ceilingUsd: "0.22708000" })], ...over,
});

test("P4-V15 a teacher dry run shows its USD budget, named payer, cost ceiling and each chunk's reservation; nothing is reserved or sent", () => {
  const [row] = teacherRows("developer", [batch({})]);
  assert.equal(row.budget, `budget 1.00000000 USD · payer ${batch({}).payerRef}`);
  assert.equal(row.ceiling, "ceiling 0.68124000 USD at rates-v1");
  assert.equal(row.plan, "2 chunks · 2 held-out samples left out · 1 not permitted to leave now (skipped)");
  assert.equal(row.status, "Dry run: nothing reserved or sent.");
  assert.deepEqual(row.chunks, ["4 samples · reservation 0.45416000 USD · not reserved, nothing sent", "2 samples · reservation 0.22708000 USD · not reserved, nothing sent"]);
  assert.match(row.labels, /synthetic \(model-generated\), never ground truth/);
  assert.match(teacherRows("developer", [batch({ ceilingUsd: null, withinBudget: false, priceVersion: null })])[0].ceiling, /^Unpriced: .*cannot be approved/);
  assert.match(teacherRows("developer", [batch({ withinBudget: false })])[0].ceiling, /^ceiling 0\.68124000 USD at rates-v1 · over the budget: it cannot be approved$/);
});

test("P4-V16 only an administrator approves, only a dry run within its budget, and never twice", () => {
  const approvable = (over: Partial<TeacherBatch>, role: "administrator" | "developer" | "viewer" = "administrator") => teacherRows(role, [batch(over)])[0].approvable;
  assert.equal(approvable({}), true);
  assert.equal(approvable({}, "developer"), false);
  assert.equal(approvable({}, "viewer"), false);
  assert.equal(approvable({ withinBudget: false }), false);
  assert.equal(approvable({ approval: { approvedBy: "u-admin", approvedAt: "2026-09-28T10:00:00Z" } }), false);
  assert.equal(teacherRows("developer", [batch({ approval: { approvedBy: "u-admin", approvedAt: "2026-09-28T10:00:00Z" } })])[0].status, "Approved by u-admin at 2026-09-28T10:00:00Z.");
});

test("P4-V17 each chunk reads as its ledger records it: held, ambiguous (never resent), settled or unknown cost, stopped, failures counted", () => {
  const approved = { approvedBy: "u-admin", approvedAt: "2026-09-28T10:00:00Z" };
  const [row] = teacherRows("administrator", [batch({ approval: approved, chunks: [
    chunk({ state: "submitted", reservedUsd: "0.45416000", sent: 3, failures: [{ sampleId: "a", reason: "malformed_label" }, { sampleId: "b", reason: "malformed_label" }, { sampleId: "c", reason: "odd" }] }),
    chunk({ state: "ambiguous", reservedUsd: "0.45416000", sent: 4 }),
    chunk({ state: "completed", reservedUsd: "0.45416000", costUsd: "0.01000000", sent: 4 }),
    chunk({ state: "completed", reservedUsd: "0.45416000", costUsd: null, sent: 4 }),
    chunk({ state: "failed", reservedUsd: "0.45416000" }),
    chunk({}),
  ] })]);
  assert.deepEqual(row.chunks, [
    "4 samples · 3 sent · 0.45416000 USD held · 3 not imported (malformed label ×2, reason not recognised ×1)",
    "4 samples · outcome unknown: 0.45416000 USD held; it is never resent, only looked up",
    "4 samples · 4 sent · cost 0.01000000 USD, as reported",
    "4 samples · 4 sent · cost unknown: the teacher reported none (never estimated)",
    "4 samples · failed: the hold is released",
    "4 samples · not reserved: the batch stopped before this chunk (payer budget or a withdrawn permission); nothing sent",
  ]);
});
