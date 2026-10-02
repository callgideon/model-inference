// P4 server actions, run for real (module hooks as tests/l/ui/actions.test.ts): the provider and role come
// from the session; the payer is this provider's; the run, export, import and checkpoint ids come from the
// form render, so a double submit is one record; the connector is never the form's; refusals are a fixed
// reason in the URL and success is a plain return to the records.
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { ROLE_CAPABILITIES, type Role } from "../../lib/auth/access.ts";
import type { FakePipelines } from "../../lib/services/pipelines/fake.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "administrator", capabilities: ROLE_CAPABILITIES.administrator };
type World = { rows: unknown[] };
const world: World = ((globalThis as unknown as { labUi: World }).labUi = { rows: [A] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    return { getAll: () => [], get: (name) => (name === "infrx-lab-session" ? { name, value: "t" } : undefined), set: () => {} };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
// AP-09: the guard reads memberships from GET /lab/v1/workspaces; this fake API answers the rows.
globalThis.fetch = (async () => new Response(JSON.stringify({ data: world.rows }))) as typeof fetch;
Object.assign(process.env, { LAB_API_URL: "https://lab-control.example", LAB_PIPELINES_PREVIEW: "1" });
const actions = await import("../../lib/services/pipelines/actions.ts");
const { pipelinesPort } = await import("../../lib/services/pipelines/port.ts");
const lab = pipelinesPort() as FakePipelines;
/** Compared as JSON text: a failure shows both values whole (no elided deep-equal diff). */
const same = (actual: unknown, expected: unknown, message?: string) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);

const P = A.provider_org_id;
const ref = (kind: string, provider: string, id: string) => `lab:${kind}:${provider}:${id}@sha256:${"a".repeat(64)}`;
const uuid = (n: number) => `${String(n).padStart(8, "0")}-0000-4000-8000-000000000000`;
const DATASET = ref("dataset", P, uuid(1));
const RUBRIC = ref("rubric", P, uuid(2));
const PAYER = ref("payer", P, uuid(3));
lab.dataset(P, DATASET, [{ sampleId: "s1", split: "train" }, { sampleId: "s2", split: "holdout" }]);

const form = (fields: Record<string, string>) => {
  const data = new FormData();
  for (const [name, value] of Object.entries(fields)) data.set(name, value);
  return data;
};
const landing = (run: Promise<unknown>) =>
  run.then(
    () => "no redirect",
    (error) => {
      const digest = String((error as { digest?: string }).digest);
      return digest.startsWith("NEXT_REDIRECT;") ? digest.split(";")[2] : `404:${digest.includes(";404")}`;
    },
  );
const as = (role: string, provider = P) => (world.rows = [{ ...A, role, provider_org_id: provider, capabilities: ROLE_CAPABILITIES[role as Role] }]);
const HERE = `/annotations?dataset=${encodeURIComponent(DATASET)}`;
const IMPORT = { importId: uuid(10), datasetRef: DATASET, rubricRef: RUBRIC, rows: JSON.stringify({ sample_id: "s1", method: "human", method_version: "h1", label: "x" }) };
const PREPARE = { externalRunId: uuid(30), datasetRef: DATASET, exportFormat: "infrx.label_export.1", exportId: uuid(20), objective: "sft", adaptation: "lora", baseModel: "marlin-2b", payerRef: PAYER, limitUsd: "25.00000000" };

test("P4-A01 actions run as the session's provider and role, whatever the form claims", async () => {
  as("developer");
  const before = lab.calls.length;
  assert.equal(await landing(actions.importLabels(form({ ...IMPORT, providerId: uuid(7), role: "administrator" }))), HERE);
  same(lab.calls.slice(before), [["importLabels", { providerId: P, providerName: "Acme", role: "developer", capabilities: ROLE_CAPABILITIES.developer }, IMPORT]]);
});

test("P4-A02 a role without the capability is refused before the pipeline service is asked", async () => {
  as("viewer");
  const before = lab.calls.length;
  assert.equal(await landing(actions.importLabels(form(IMPORT))), `${HERE}&refused=denied`);
  assert.equal(await landing(actions.prepareTraining(form(PREPARE))), "/training?refused=denied");
  assert.equal(await landing(actions.runAction(form({ op: "submit", externalRunId: uuid(30) }))), "/training?refused=denied");
  as("developer");
  assert.equal(await landing(actions.assignReviewer(form({ datasetRef: DATASET, sampleId: "s1", reviewerId: uuid(99), rubricRef: RUBRIC }))), `${HERE}&refused=denied`);
  assert.equal(lab.calls.length, before);
});

test("P4-A03 malformed input, another provider's payer or dataset, or an unknown op never reaches the pipeline service", async () => {
  as("administrator");
  const before = lab.calls.length;
  for (const bad of [{ ...PREPARE, limitUsd: "25" }, { ...PREPARE, limitUsd: "-1.00000000" }, { ...PREPARE, payerRef: ref("payer", uuid(7), uuid(3)) },
    { ...PREPARE, externalRunId: "run-1" }, { ...PREPARE, objective: "rlhf" }, { ...PREPARE, adaptation: "qlora" }, { ...PREPARE, exportFormat: "csv" },
    { ...PREPARE, datasetRef: ref("dataset", uuid(7), uuid(1)) }, { ...PREPARE, baseModel: "" }])
    assert.equal(await landing(actions.prepareTraining(form(bad))), "/training?refused=invalid", JSON.stringify(bad));
  assert.equal(await landing(actions.runAction(form({ op: "resubmit", externalRunId: uuid(30) }))), "/training?refused=invalid");
  assert.equal(await landing(actions.exportLabels(form({ exportId: uuid(20), datasetRef: DATASET, adapter: "dpo", ttlS: "3600" }))), `${HERE}&refused=invalid`);
  assert.equal(await landing(actions.exportLabels(form({ exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: "604801" }))), `${HERE}&refused=invalid`);
  assert.equal(await landing(actions.reviewLabel(form({ datasetRef: DATASET, annotationRef: ref("annotation", P, uuid(5)), decision: "accepted", rubricRef: RUBRIC, correction: '"x"' }))), `${HERE}&refused=invalid`);
  assert.equal(await landing(actions.reviewLabel(form({ datasetRef: DATASET, annotationRef: ref("annotation", P, uuid(5)), decision: "superseded", rubricRef: RUBRIC }))), `${HERE}&refused=invalid`);
  assert.equal(await landing(actions.importCheckpoint(form({ externalRunId: uuid(30), checkpointId: uuid(40), artifactKey: `lab/${P}/training/${uuid(30)}/a`, artifactDigest: "latest" }))), "/training?refused=invalid");
  assert.equal(await landing(actions.importLabels(form({ ...IMPORT, importId: "" }))), `${HERE}&refused=invalid`);
  assert.equal(await landing(actions.importLabels(form({ ...IMPORT, datasetRef: "not-a-ref" }))), "/annotations?refused=invalid");
  assert.equal(lab.calls.length, before);
});

test("P4-A04 the training connector is always the manual bundle, never the form's; a double submit is one run", async () => {
  as("developer");
  value(await lab.exportLabels({ providerId: P, role: "developer" }, { exportId: uuid(20), datasetRef: DATASET, adapter: "sft.1", ttlS: 3600 }));
  const before = lab.calls.length;
  assert.equal(await landing(actions.prepareTraining(form({ ...PREPARE, connector: "vendor-x" }))), "/training");
  assert.equal(await landing(actions.prepareTraining(form({ ...PREPARE, connector: "vendor-x" }))), "/training");
  const sent = lab.calls.slice(before) as [string, unknown, { connector?: unknown; config: unknown }][];
  same(sent.map((c) => [c[0], c[2].connector, c[2].config]), [
    ["prepare", undefined, { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }], ["prepare", undefined, { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }],
  ]);
  const runs = value(await lab.runs({ providerId: P, role: "developer" }));
  same(runs.map((r) => [r.externalRunId, r.connector, r.limitUsd, r.payerRef]), [[uuid(30), "manual-bundle", "25.00000000", PAYER]]);
});

test("P4-A05 the service's refusal is carried as its reason; its success is a plain return to the records", async () => {
  as("developer");
  assert.equal(await landing(actions.runAction(form({ op: "finish", externalRunId: uuid(30) }))), "/training?refused=conflict");
  assert.equal(await landing(actions.runAction(form({ op: "submit", externalRunId: uuid(30) }))), "/training");
  assert.equal(await landing(actions.runAction(form({ op: "finish", externalRunId: uuid(30) }))), "/training");
  assert.equal(await landing(actions.approveCheckpoint(form({ externalRunId: uuid(30), checkpointId: uuid(40) }))), "/training?refused=conflict");
  assert.equal(await landing(actions.importCheckpoint(form({ externalRunId: uuid(30), checkpointId: uuid(40), artifactKey: `lab/${P}/training/${uuid(30)}/a`, artifactDigest: `sha256:${"c".repeat(64)}` }))), "/training");
  assert.equal(await landing(actions.adjudicateSample(form({ datasetRef: DATASET, sampleId: "s1", value: '"x"', rubricRef: RUBRIC }))), `${HERE}&refused=conflict`);
});

test("P4-A06 a consumer-only user gets a 404 from every action and the pipeline service is never asked", async () => {
  world.rows = [];
  const before = lab.calls.length;
  for (const run of [actions.importLabels(form(IMPORT)), actions.prepareTraining(form(PREPARE)), actions.runAction(form({ op: "submit", externalRunId: uuid(30) })),
    actions.approveCheckpoint(form({ externalRunId: uuid(30), checkpointId: uuid(40) }))])
    assert.equal(await landing(run), "404:true");
  assert.equal(lab.calls.length, before);
});

const TEACH = { batchId: uuid(50), datasetRef: DATASET, rubricRef: RUBRIC, teacherModel: "claude-opus-5", promptVersion: "teach-v1", payerRef: PAYER, budgetUsd: "1.00000000", chunkSize: "2" };

test("P4-A07 a teacher dry run and its approval: the session's actor and the form's batch id; a developer never approves; malformed input never reaches the service", async () => {
  as("developer");
  const before = lab.calls.length;
  assert.equal(await landing(actions.planTeachers(form({ ...TEACH, providerId: uuid(7), live: "1" }))), "/training");
  same(lab.calls.slice(before), [["planTeachers", { providerId: P, providerName: "Acme", role: "developer", capabilities: ROLE_CAPABILITIES.developer }, { ...TEACH, chunkSize: 2 }]]);
  assert.equal(await landing(actions.approveTeachers(form({ batchId: uuid(50) }))), "/training?refused=denied");
  for (const bad of [{ budgetUsd: "1" }, { budgetUsd: "1.00000000 CREDIT" }, { payerRef: ref("payer", uuid(7), uuid(3)) }, { datasetRef: ref("dataset", uuid(7), uuid(1)) },
    { chunkSize: "0" }, { chunkSize: "201" }, { chunkSize: "2.5" }, { batchId: "b-1" }, { teacherModel: "" }, { promptVersion: "" }, { rubricRef: "r" }])
    assert.equal(await landing(actions.planTeachers(form({ ...TEACH, ...bad }))), "/training?refused=invalid", JSON.stringify(bad));
  as("administrator");
  assert.equal(await landing(actions.approveTeachers(form({ batchId: "b-1" }))), "/training?refused=invalid");
  assert.equal(lab.calls.length, before + 1);
  assert.equal(await landing(actions.approveTeachers(form({ batchId: uuid(50) }))), "/training");
  same(lab.calls.at(-1), ["approveTeachers", { providerId: P, providerName: "Acme", role: "administrator", capabilities: ROLE_CAPABILITIES.administrator }, uuid(50)]);
  assert.equal(await landing(actions.approveTeachers(form({ batchId: uuid(59) }))), "/training?refused=not_found");
});

function value<T>(r: { ok: true; value: T } | { ok: false; reason: string }): T {
  assert.ok(r.ok, `refused: ${!r.ok && r.reason}`);
  return r.value;
}
