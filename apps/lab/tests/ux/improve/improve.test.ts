// UX-09 (UX-T12 improvement safety) over the pipelines preview stand-in (lib/services/pipelines/fake.ts,
// the WR-P4-1 route's rules): a teacher dry run sends nothing and only an administrator is offered the
// paid step; an ambiguous training submission is offered a lookup that never submits again, and only to
// a writer; the run listing's gate names what stays off. Each case names what it catches.
import assert from "node:assert/strict";
import test from "node:test";
import { canLookUp, GATED_COPY, LOOKUP_LABEL } from "../../../app/(provider)/training/view.ts";
import { FakePipelines } from "../../../lib/services/pipelines/fake.ts";
import { TEACHER_CHUNK_STATES, type Actor, type Result, type TrainingRun } from "../../../lib/services/pipelines/port.ts";
import { runRows, teacherRows } from "../../../lib/services/pipelines/view.ts";

const A = "11111111-1111-4111-8111-111111111111";
const uuid = (n: number) => `${String(n).padStart(8, "0")}-0000-4000-8000-000000000000`;
const ref = (kind: string, id: string) => `lab:${kind}:${A}:${id}@sha256:${"a".repeat(64)}`;
const DATASET = ref("dataset", uuid(1));
const RUBRIC = ref("rubric", uuid(2));
const PAYER = ref("payer", uuid(3));
const dev: Actor = { providerId: A, role: "developer" };
const admin: Actor = { providerId: A, role: "administrator" };
const value = <T>(r: Result<T>): T => {
  if (!r.ok) throw new Error(`refused: ${r.reason}`);
  return r.value;
};
const STATES: TrainingRun["state"][] = ["prepared", "submitting", "submitted", "ambiguous", "completed", "failed", "cancelled"];

function world() {
  const lab = new FakePipelines();
  lab.dataset(A, DATASET, [{ sampleId: "s1", split: "train" }, { sampleId: "s2", split: "validation" }, { sampleId: "s3", split: "holdout" }]);
  return lab;
}

test("UX09-V01 only an ambiguous or stuck submission is offered the lookup, only to a writer, and the lookup never reads as a submit", () => {
  assert.deepEqual(STATES.filter((s) => canLookUp(s, true)), ["submitting", "ambiguous"]);
  assert.deepEqual(STATES.filter((s) => canLookUp(s, false)), [], "a viewer is offered no action");
  assert.doesNotMatch(LOOKUP_LABEL, /^Submit|again$/i, "the lookup is not labelled as a submission");
  assert.match(LOOKUP_LABEL, /never submits again/);
});

test("UX09-J01 an ambiguous training submission: the page offers the lookup, never Submit; the lookup only looks up, and a found job moves on", async () => {
  const lab = world();
  value(await lab.importLabels(dev, { importId: uuid(10), datasetRef: DATASET, rubricRef: RUBRIC, rows: JSON.stringify({ sample_id: "s1", method: "human", method_version: "h1", label: "yes" }) }));
  for (const l of value(await lab.labels(dev, DATASET))) value(await lab.review(dev, { datasetRef: DATASET, annotationRef: l.annotationRef, decision: "accepted", rubricRef: RUBRIC, correction: null }));
  const exported = value(await lab.exportLabels(dev, { exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 }));
  const run = value(await lab.prepare(dev, { externalRunId: uuid(30), datasetRef: DATASET, exportFormat: "infrx.label_export.1", exportId: exported.exportId, config: { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }, payerRef: PAYER, limitUsd: "25.00000000" }));
  lab.ambiguous(run.externalRunId);
  const [ambiguous] = value(await lab.runs(dev));
  assert.deepEqual(runRows("developer", [ambiguous])[0].actions, [], "no Submit, Finish or Cancel on an unknown outcome");
  assert.equal(canLookUp(ambiguous.state, true), true, "the page offers the lookup instead");
  // the lookup form posts the run's own id to `submit`, which on an unknown outcome is a lookup only (P3 reconcile)
  value(await lab.submit(dev, run.externalRunId));
  assert.deepEqual([lab.submissions, lab.lookups, value(await lab.runs(dev))[0].state], [0, 1, "ambiguous"], "not found: still ambiguous, nothing submitted");
  lab.found(run.externalRunId);
  value(await lab.submit(dev, run.externalRunId));
  assert.deepEqual([lab.submissions, lab.lookups, value(await lab.runs(dev))[0].state], [0, 2, "submitted"], "found: submitted, still never submitted twice");
  assert.equal(canLookUp("submitted", true), false, "a known outcome offers no lookup");
});

test("UX09-J02 a teacher dry run sends and reserves nothing; only an administrator is offered the paid step, and an approval resumes without resending", async () => {
  const lab = world();
  const input = { batchId: uuid(40), datasetRef: DATASET, rubricRef: RUBRIC, teacherModel: "claude-opus-5", promptVersion: "p1", payerRef: PAYER, budgetUsd: "10.00000000", chunkSize: 1 };
  const plan = value(await lab.planTeachers(dev, input));
  assert.deepEqual([lab.teacherSends, plan.approval, plan.chunks.map((c) => c.state)], [0, null, ["unreserved", "unreserved"]], "the dry run sends nothing and the holdout is left out");
  const [asDev] = teacherRows("developer", [plan]);
  assert.deepEqual([asDev.status, asDev.approvable], ["Dry run: nothing reserved or sent.", false]);
  assert.equal(teacherRows("administrator", [plan])[0].approvable, true);
  value(await lab.approveTeachers(admin, input.batchId));
  value(await lab.approveTeachers(admin, input.batchId));
  assert.equal(lab.teacherSends, 2, "two chunks sent once each: the second approval resumed and resent nothing");
  assert.ok(TEACHER_CHUNK_STATES.includes("ambiguous"));
});

test("UX09-V02 the gate copy says what stays off while the run records cannot be read, and that teacher labelling is read separately", () => {
  assert.match(GATED_COPY, /preparing a bundle and importing a checkpoint are not offered/i);
  assert.match(GATED_COPY, /teacher labelling .* read separately/i);
});
