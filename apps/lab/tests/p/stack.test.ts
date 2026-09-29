// P4 swap, real evidence: the journeys J01-J06 rerun through the Lab's pipelines adapter against
// lab-api's `/lab/v1/pipelines` as merged, over the real D7 and L2 on the task-local PostgreSQL
// (`backend.py`, key p1); J06's teacher batches over the real D8 `PgTeacherLedger` and the local
// teacher fake on p2's port. Skipped unless LAB_P4_REAL=1 (needs Docker and the p1/p2 keys):
//   LAB_P4_REAL=1 INFRX_D_TASK=p1 node --test tests/p/stack.test.ts
// Outside the mutant suite (like V1M's stack): its oracle is the real route, not Lab code.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import { httpPipelines } from "../../lib/services/pipelines/http.ts";
import type { Actor, Result } from "../../lib/services/pipelines/port.ts";
import { checkpointRows, labelRows, runRows, teacherRows } from "../../lib/services/pipelines/view.ts";

const REAL = process.env.LAB_P4_REAL === "1";
const lab = resolve(import.meta.dirname, "../..");
type Who = "dev" | "admin" | "viewer" | "other_dev" | "consumer";
type World = {
  A: string; B: string; dataset: string; rubric: string; payer: string; train: string[]; holdout: string[]; validation: string[];
  users: Record<Who, string>; tokens: Record<Who, string>; teacher: string;
};

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/p/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
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

const value = <T>(r: Result<T>): T => {
  assert.ok(r.ok, `refused: ${JSON.stringify(r)}`);
  return r.value;
};
const reason = (r: Result<unknown>) => (r.ok ? "ok" : r.reason);
const same = (actual: unknown, expected: unknown, message?: string) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const lines = (...rows: object[]) => rows.map((l) => JSON.stringify(l)).join("\n");
const uuid = (n: number) => `${String(n).padStart(8, "0")}-0000-4000-8000-000000000000`;
const CONFIG = { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" } as const;

test("P4-S01..S06 the pipelines journeys on the real route: labels, the manual run to an eligible checkpoint, rejected checkpoints, ambiguous and revoked, teacher batches, unauthorized", { skip: !REAL && "LAB_P4_REAL=1 (Docker, p1)" }, async (t) => {
  const { url, world: w, stop } = await backend();
  t.after(stop);
  const as = (who: Who) => httpPipelines({ baseUrl: url, token: async () => w.tokens[who] });
  const hook = async (path: string, body: unknown = {}) => {
    const r = await fetch(`${url}/_test/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    assert.equal(r.status, 200, path);
    return r.json();
  };
  const A: Actor = { providerId: w.A, role: "developer" };
  const admin: Actor = { providerId: w.A, role: "administrator" };
  const viewer: Actor = { providerId: w.A, role: "viewer" };
  const other: Actor = { providerId: w.B, role: "developer" };
  const D = w.dataset;
  const [s1, s2, s3, s4] = w.train;
  let exportId = "";
  let runRef = "";

  await t.test("S01 (J01) import → review with a correction → adjudication → train-only export: methods kept apart, lineage and omissions recorded", async () => {
    const importing = { importId: uuid(10), datasetRef: D, rubricRef: w.rubric, rows: lines(
      { sample_id: s1, method: "model", method_version: "m1", label: { answer: "yes" } },
      { sample_id: s1, method: "human", method_version: "h1", label: { answer: "no" } },
      { sample_id: s2, method: "human", method_version: "h1", label: { answer: "no" }, ground_truth: true },
      { sample_id: uuid(77), method: "human", method_version: "h1", label: { answer: "no" } },
      { sample_id: s3, method: "annotator", method_version: "h1", label: { answer: "no" } },
    ) };
    const receipt = value(await as("dev").importLabels(A, importing));
    assert.equal(receipt.accepted, 2, JSON.stringify(receipt));
    same(receipt.rejected.map((r) => r.row), [3, 4, 5]);
    same(value(await as("dev").importLabels(A, importing)), receipt, "a replay is the stored receipt");
    assert.equal(reason(await as("dev").importLabels(A, { ...importing, rows: lines({ sample_id: s1, method: "model", method_version: "m1", label: { answer: "x" } }) })), "conflict");
    same(value(await as("dev").imports(A)).map((r) => r.importId), [uuid(10)]);
    let labels = value(await as("dev").labels(A, D));
    same(labels.map((l) => [l.method, l.groundTruth, l.state]).sort(), [["imported", false, "submitted"], ["synthetic", false, "submitted"]]);
    same(value(await as("dev").disagreements(A, D)), [{ sampleId: s1, annotationRefs: labels.map((l) => l.annotationRef).sort() }]);
    same(labelRows("developer", labels).map((r) => r.truth), ["not ground truth", "not ground truth"]);

    value(await as("admin").assign(admin, { datasetRef: D, sampleId: s1, reviewerId: w.users.dev, rubricRef: w.rubric }));
    const synthetic = labels.find((l) => l.method === "synthetic")!;
    value(await as("dev").review(A, { datasetRef: D, annotationRef: synthetic.annotationRef, decision: "rejected", rubricRef: w.rubric, correction: '{"answer":"no"}' }));
    labels = value(await as("dev").labels(A, D));
    const human = labels.filter((l) => l.method === "human");
    same(human.map((l) => [l.groundTruth, l.state, l.value]), [[true, "accepted", '{"answer":"no"}']], "only a human review is ground truth");
    assert.equal(labels.find((l) => l.annotationRef === synthetic.annotationRef)!.state, "rejected");
    assert.equal(reason(await as("dev").review(A, { datasetRef: D, annotationRef: synthetic.annotationRef, decision: "accepted", rubricRef: w.rubric, correction: null })), "conflict");

    value(await as("dev").importLabels(A, { importId: uuid(11), datasetRef: D, rubricRef: w.rubric, rows: lines(
      { sample_id: s2, method: "model", method_version: "m1", label: { answer: "a" } }, { sample_id: s2, method: "human", method_version: "h1", label: { answer: "b" } },
      { sample_id: s3, method: "human", method_version: "h1", label: { answer: "c" } }, { sample_id: s4, method: "human", method_version: "h1", label: { answer: "d" } },
      { sample_id: w.holdout[0], method: "human", method_version: "h1", label: { answer: "e" } },
    ) }));
    value(await as("admin").assign(admin, { datasetRef: D, sampleId: s2, reviewerId: w.users.dev, rubricRef: w.rubric }));
    value(await as("dev").adjudicate(A, { datasetRef: D, sampleId: s2, value: '{"answer":"b"}', rubricRef: w.rubric }));
    labels = value(await as("dev").labels(A, D));
    same(labels.filter((l) => l.sampleId === s2).map((l) => [l.method, l.state]).sort(), [["human", "accepted"], ["imported", "rejected"], ["synthetic", "rejected"]]);
    for (const l of labels.filter((x) => x.state === "submitted")) {
      value(await as("admin").assign(admin, { datasetRef: D, sampleId: l.sampleId, reviewerId: w.users.dev, rubricRef: w.rubric }));
      value(await as("dev").review(A, { datasetRef: D, annotationRef: l.annotationRef, decision: "accepted", rubricRef: w.rubric, correction: null }));
    }
    const exported = value(await as("dev").exportLabels(A, { exportId: uuid(20), datasetRef: D, adapter: "sft.1", ttlS: 3600 }));
    same(exported.lineage.map((x) => x.sampleId), [s1, s2, s3, s4].sort(), "train samples only");
    same(exported.lineage.map((x) => x.methods), [s1, s2, s3, s4].sort().map((s) => (s === s1 ? ["human", "imported"] : s === s2 ? ["human"] : ["imported"])), "methods kept apart");
    assert.ok(exported.omitted.some((o) => o.sampleId === w.holdout[0]), "the held-out label is omitted, with its reason");
    assert.deepEqual(value(await as("dev").exportLabels(A, { exportId: uuid(20), datasetRef: D, adapter: "sft.1", ttlS: 3600 })), exported);
    assert.equal(reason(await as("dev").exportLabels(A, { exportId: uuid(20), datasetRef: D, adapter: "preference.1", ttlS: 3600 })), "conflict");
    exportId = exported.exportId;
  });

  await t.test("S02 (J02) manual bundle → submit (nothing reserved) → finish → checkpoint → held-out evaluation → eligible, all replay-safe", async () => {
    const input = { externalRunId: uuid(30), datasetRef: D, exportFormat: "infrx.label_export.1", exportId, config: CONFIG, payerRef: w.payer, limitUsd: "25.00000000" };
    const run = value(await as("dev").prepare(A, input));
    same([run.state, run.connector, run.limitUsd, run.payerRef, run.reservedUsd, run.settled, run.costUsd], ["prepared", "manual-bundle", "25.00000000", w.payer, "0.00000000", false, null]);
    same(value(await as("dev").prepare(A, input)), run);
    assert.equal(reason(await as("dev").prepare(A, { ...input, limitUsd: "99.00000000" })), "conflict");
    const bundle = JSON.parse(value(await as("dev").bundle(A, uuid(30))));
    same([bundle.external_run_ref, bundle.holdout], [run.runRef, run.holdout]);
    same(runRows("developer", [run])[0].actions, ["submit", "cancel"]);
    runRef = run.runRef;
    assert.equal(value(await as("dev").submit(A, uuid(30))).state, "submitted");
    assert.equal(value(await as("dev").submit(A, uuid(30))).state, "submitted");
    assert.equal(value(await as("dev").runs(A))[0].reservedUsd, "0.00000000");
    assert.equal(value(await as("dev").finish(A, uuid(30))).state, "completed");
    assert.equal(value(await as("dev").finish(A, uuid(30))).state, "completed");
    const art = await hook("artifact", { external_run_id: uuid(30), name: "adapter.json" });
    const good = { externalRunId: uuid(30), checkpointId: uuid(40), artifactKey: art.key, artifactDigest: art.digest };
    assert.equal(reason(await as("dev").approve(A, { externalRunId: uuid(30), checkpointId: uuid(40) })), "conflict", "no checkpoint yet");
    const ckpt = value(await as("dev").importCheckpoint(A, good));
    same([ckpt.state, ckpt.evaluation?.state, ckpt.evaluation?.split, ckpt.evaluation?.holdoutSha256], ["validated", "queued", "holdout", run.holdout.sha256]);
    same(value(await as("dev").importCheckpoint(A, good)), ckpt, "a redelivery keeps the first outcome");
    assert.equal(reason(await as("dev").approve(A, { externalRunId: uuid(30), checkpointId: uuid(40) })), "conflict", "not evaluated yet");
    await hook("evaluated", { checkpoint_id: uuid(40), state: "succeeded" });
    assert.equal(value(await as("dev").approve(A, { externalRunId: uuid(30), checkpointId: uuid(40) })).eligible, true);
    same(value(await as("dev").checkpoints(A)).map((c) => [c.checkpointId, c.eligible]), [[uuid(40), true]]);
  });

  await t.test("S03 (J03) a mismatched or missing checkpoint is rejected with its reason and never evaluated; a redelivery keeps the first outcome", async () => {
    const art = await hook("artifact", { external_run_id: uuid(30), name: "b.json" });
    const mismatch = value(await as("dev").importCheckpoint(A, { externalRunId: uuid(30), checkpointId: uuid(43), artifactKey: art.key, artifactDigest: `sha256:${"d".repeat(64)}` }));
    same([mismatch.state, mismatch.reason, mismatch.evaluation], ["rejected", "digest_mismatch", null]);
    same(checkpointRows("developer", [mismatch], value(await as("dev").runs(A)))[0].state, "Rejected: the artifact's bytes do not match the declared digest. Never evaluated.");
    same(value(await as("dev").importCheckpoint(A, { externalRunId: uuid(30), checkpointId: uuid(43), artifactKey: art.key, artifactDigest: `sha256:${"d".repeat(64)}` })), mismatch);
    const missing = value(await as("dev").importCheckpoint(A, { externalRunId: uuid(30), checkpointId: uuid(42), artifactKey: `lab/${w.A}/training/${uuid(30)}/gone`, artifactDigest: art.digest }));
    same([missing.state, missing.reason, missing.evaluation], ["rejected", "missing_artifact", null]);
    assert.equal(reason(await as("dev").importCheckpoint(A, { externalRunId: uuid(30), checkpointId: uuid(44), artifactKey: `lab/${w.A}/training/${uuid(31)}/a`, artifactDigest: art.digest })), "invalid");
    assert.equal(reason(await as("dev").approve(A, { externalRunId: uuid(30), checkpointId: uuid(43) })), "conflict");
  });

  await t.test("S04 (J04) an ambiguous submission reads as recorded and is resolved by a lookup (the revoked-grant half closes S05)", async () => {
    const input = { externalRunId: uuid(31), datasetRef: D, exportFormat: "infrx.label_export.1", exportId, config: CONFIG, payerRef: w.payer, limitUsd: "5.00000000" };
    value(await as("dev").prepare(A, input));
    value(await as("dev").submit(A, uuid(31)));
    await hook("ambiguous", { external_run_id: uuid(31) });
    assert.equal(value(await as("dev").runs(A)).find((r) => r.externalRunId === uuid(31))!.state, "ambiguous", "the page reads the ledger's state");
    assert.equal(value(await as("dev").submit(A, uuid(31))).state, "submitted", "the manual bundle's lookup finds it");
  });

  await t.test("S06 (J06) teacher batch on the real D8 ledger: a dry run sends nothing; only an administrator approves within the budget; a double click is one batch; ambiguous and failures read back", async () => {
    const input = { batchId: uuid(60), datasetRef: D, rubricRef: w.rubric, teacherModel: w.teacher, promptVersion: "teach-v1", payerRef: w.payer, budgetUsd: "1.00000000", chunkSize: 4 };
    const planned = value(await as("dev").planTeachers(A, input));
    same([planned.ceilingUsd, planned.withinBudget, planned.holdout, planned.notPermitted, planned.approval, planned.chunks.map((c) => [c.samples, c.ceilingUsd, c.state])],
      ["0.68124000", true, 2, 0, null, [[4, "0.45416000", "unreserved"], [2, "0.22708000", "unreserved"]]]);
    same(teacherRows("administrator", [planned])[0].status, "Dry run: nothing reserved or sent.");
    assert.equal((await hook("teacher-mode", { mode: "ok" })).posts, 0, "a dry run sends nothing");
    same(value(await as("admin").planTeachers(admin, input)), planned, "the same batch id again is the stored batch");
    assert.equal(reason(await as("dev").planTeachers(A, { ...input, chunkSize: 2 })), "conflict");
    for (const bad of [{ payerRef: w.payer.replace(w.A, w.B) }, { budgetUsd: "1" }]) assert.equal(reason(await as("dev").planTeachers(A, { ...input, batchId: uuid(63), ...bad })), "invalid");
    assert.equal(reason(await as("dev").approveTeachers(A, uuid(60))), "denied");
    assert.equal(reason(await as("viewer").approveTeachers(viewer, uuid(69))), "not_found", "the batch before the role (R183)");
    assert.equal(reason(await as("other_dev").approveTeachers(A, uuid(60))), "not_found");
    assert.equal(reason(await as("viewer").teacherBatches(viewer)), "denied");
    value(await as("dev").planTeachers(A, { ...input, batchId: uuid(61), budgetUsd: "0.50000000" }));
    assert.equal(reason(await as("admin").approveTeachers(admin, uuid(61))), "conflict", "over its budget");
    const approved = value(await as("admin").approveTeachers(admin, uuid(60)));
    same([approved.approval?.approvedBy, approved.chunks.map((c) => [c.state, c.reservedUsd, c.sent])], [w.users.admin, [["submitted", "0.45416000", 4], ["submitted", "0.22708000", 2]]]);
    same(value(await as("admin").approveTeachers(admin, uuid(60))), approved, "a double click resumes and sends nothing more");
    assert.equal((await hook("teacher-mode", { mode: "drop" })).posts, 2);
    value(await as("dev").planTeachers(A, { ...input, batchId: uuid(62), chunkSize: 6 }));
    const lost = value(await as("admin").approveTeachers(admin, uuid(62)));
    same(lost.chunks.map((c) => [c.state, c.reservedUsd]), [["ambiguous", "0.68124000"]], "the lost answer is held");
    value(await as("admin").approveTeachers(admin, uuid(62)));
    assert.equal((await hook("teacher-mode", { mode: "ok" })).posts, 3, "an ambiguous chunk is never resent");
    await hook("teacher-failures", { run_id: approved.chunks[0].runId, failures: [{ sample_id: s1, reason: "malformed_label" }, { sample_id: s2, reason: "duplicate" }] });
    const listed = value(await as("dev").teacherBatches(A)).find((b) => b.batchId === uuid(60))!;
    same(listed.chunks[0].failures, [{ sampleId: s1, reason: "malformed_label" }, { sampleId: s2, reason: "duplicate" }]);
    same(teacherRows("developer", [listed])[0].chunks[0], "4 samples · 4 sent · 0.45416000 USD held · 2 not imported (malformed label ×1, duplicate ×1)");
  });

  await t.test("S05 (J05) unauthorized variants: another provider, a viewer, a consumer, a developer assigning, a foreign payer, an expired export", async () => {
    for (const r of [await as("other_dev").runs(A), await as("other_dev").labels(A, D), await as("other_dev").submit(A, uuid(30))]) assert.equal(reason(r), "not_found");
    for (const r of [await as("other_dev").runs(other), await as("other_dev").imports(other), await as("other_dev").exports(other), await as("other_dev").checkpoints(other)] as Result<unknown[]>[]) same(value(r), []);
    assert.equal(reason(await as("other_dev").bundle(other, uuid(30))), "not_found");
    for (const r of [await as("viewer").labels(viewer, D), await as("viewer").runs(viewer), await as("viewer").submit(viewer, uuid(30)), await as("consumer").runs(A)]) assert.equal(reason(r), "denied");
    assert.equal(reason(await as("dev").assign(A, { datasetRef: D, sampleId: s1, reviewerId: w.users.dev, rubricRef: w.rubric })), "denied");
    const input = { datasetRef: D, exportFormat: "infrx.label_export.1", exportId, config: CONFIG, limitUsd: "1.00000000" };
    assert.equal(reason(await as("dev").prepare(A, { ...input, externalRunId: uuid(33), payerRef: w.payer.replace(w.A, w.B) })), "invalid");
    const short = value(await as("dev").exportLabels(A, { exportId: uuid(21), datasetRef: D, adapter: "sft.1", ttlS: 60 }));
    await hook("advance", { seconds: 61 });
    assert.equal(reason(await as("dev").prepare(A, { ...input, exportId: short.exportId, externalRunId: uuid(34), payerRef: w.payer })), "gone");
    same(value(await as("dev").runs(A)).map((r) => r.runRef).includes(runRef), true);
    // (J04) a revoked grant stops a submit: C1 revokes through lab-sql's real RPC, last, as it ends the world's grant
    value(await as("dev").prepare(A, { ...input, externalRunId: uuid(32), payerRef: w.payer }));
    await hook("revoke");
    assert.equal(reason(await as("dev").submit(A, uuid(32))), "denied");
    assert.equal(value(await as("dev").runs(A)).find((r) => r.externalRunId === uuid(32))!.state, "prepared");
  });
});
