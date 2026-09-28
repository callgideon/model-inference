// WR-P4-1: the HTTP adapter speaks lab-api-2's `/lab/v1/pipelines` wire exactly (tests/g/lab_pipelines on
// the Python side): the session token, the actor's provider, bodies and records renamed between the
// route's snake_case and the port's camelCase and nothing else, the bundle as text, the fixed refusals.
import assert from "node:assert/strict";
import test from "node:test";
import { httpPipelines } from "../../lib/services/pipelines/http.ts";

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "administrator" as const };
type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answer: (seen: Seen) => Response | Promise<Response>) {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpPipelines({ baseUrl: "https://api.test/", token: "eyJ0.a.b", fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const RUN = { external_run_id: "e 1", run_ref: "lab:external_run:x", export: { format: "infrx.label_export.1", export_id: "x1", sha256: "ab" },
  config: { objective: "sft", adaptation: "lora", base_model: "marlin-2b" }, holdout: { size: 2, sha256: "cd" }, limit_usd: "25.00000000", reserved_usd: "0.00000000", cost_usd: null };
const LABEL = { annotation_ref: "lab:annotation:a", sample_id: "s", ground_truth: false, value: "{\"a_b\":1}", reviewer_id: null };
const BUNDLE = { format: "infrx.training_bundle.1", external_run_ref: "lab:external_run:x", train: ["s"] };

test("P4-H01 every call is the session's token and the actor's provider on its route; keys are renamed both ways, values untouched", async () => {
  const { seen, port } = server((s) => {
    if (s.url.includes("/bundle")) return json(BUNDLE);
    if (s.method === "GET") return json({ data: s.url.includes("/labels") ? [LABEL] : [RUN] });
    return json(s.url.includes("/assignments") ? {} : RUN);
  });
  assert.deepEqual(await port.labels(A, "lab:dataset:d 1"), { ok: true, value: [
    { annotationRef: "lab:annotation:a", sampleId: "s", groundTruth: false, value: "{\"a_b\":1}", reviewerId: null }] });
  const run = { externalRunId: "e 1", runRef: "lab:external_run:x", export: { format: "infrx.label_export.1", exportId: "x1", sha256: "ab" },
    config: { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }, holdout: { size: 2, sha256: "cd" }, limitUsd: "25.00000000", reservedUsd: "0.00000000", costUsd: null };
  assert.deepEqual(await port.runs(A), { ok: true, value: [run] });
  assert.deepEqual(await port.bundle(A, "e 1"), { ok: true, value: JSON.stringify(BUNDLE) });
  assert.deepEqual(await port.prepare(A, { externalRunId: "e 1", datasetRef: "lab:dataset:d", exportFormat: "infrx.label_export.1", exportId: "x1",
    config: { objective: "sft", adaptation: "lora", baseModel: "marlin-2b" }, payerRef: "lab:payer:p", limitUsd: "25.00000000" }), { ok: true, value: run });
  assert.deepEqual(await port.assign(A, { datasetRef: "lab:dataset:d", sampleId: "s", reviewerId: "u", rubricRef: "lab:rubric:r" }), { ok: true, value: null });
  await port.exportLabels(A, { exportId: "x1", datasetRef: "lab:dataset:d", adapter: "sft.1", ttlS: 60 });
  await port.submit(A, "e 1");
  await port.approve(A, { externalRunId: "e 1", checkpointId: "c 1" });
  await port.disagreements(A, "lab:dataset:d");
  for (const read of ["imports", "exports", "checkpoints"] as const) await port[read](A);
  const q = `?provider_org_id=${A.providerId}`;
  const u = (path: string, more = "") => `https://api.test/lab/v1/pipelines/${path}${q}${more}`;
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["GET", u("labels", "&dataset_ref=lab%3Adataset%3Ad%201")], ["GET", u("training-runs")], ["GET", u("training-runs/e%201/bundle")],
    ["POST", u("training-runs")], ["POST", u("assignments")], ["POST", u("label-exports")], ["POST", u("training-runs/e%201/submit")],
    ["POST", u("checkpoints/c%201/approve")], ["GET", u("disagreements", "&dataset_ref=lab%3Adataset%3Ad")],
    ["GET", u("label-imports")], ["GET", u("label-exports")], ["GET", u("checkpoints")],
  ]);
  assert.ok(seen.every((s) => s.headers.authorization === "Bearer eyJ0.a.b"));
  assert.deepEqual(seen[3].body, { external_run_id: "e 1", dataset_ref: "lab:dataset:d", payer_ref: "lab:payer:p",
    export: { format: "infrx.label_export.1", export_id: "x1" }, config: { objective: "sft", adaptation: "lora", base_model: "marlin-2b" }, limit: "25.00000000" });
  assert.deepEqual(seen[4].body, { dataset_ref: "lab:dataset:d", sample_id: "s", reviewer_id: "u", rubric_ref: "lab:rubric:r" });
  assert.deepEqual(seen[5].body, { export_id: "x1", dataset_ref: "lab:dataset:d", adapter: "sft.1", ttl_s: 60 });
  assert.deepEqual([seen[6].body, seen[7].body], [undefined, { external_run_id: "e 1" }]);
  assert.equal(seen[3].headers["content-type"], "application/json");
});

test("P4-H02 the route's refusals are the port's reasons (410 is gone); anything else, or no answer, is unavailable", async () => {
  const cases: [number, string][] = [[401, "denied"], [403, "denied"], [404, "not_found"], [409, "conflict"], [410, "gone"], [422, "invalid"], [503, "unavailable"], [500, "unavailable"]];
  for (const [status, reason] of cases) {
    const { port } = server(() => json({ refusal: "x" }, status));
    assert.deepEqual(await port.submit(A, "e1"), { ok: false, reason }, String(status));
  }
  const down = server(() => { throw new TypeError("fetch failed"); });
  assert.deepEqual(await down.port.runs(A), { ok: false, reason: "unavailable" });
  const garbled = server(() => new Response("<html>", { status: 200 }));
  assert.deepEqual(await garbled.port.checkpoints(A), { ok: false, reason: "unavailable" });
});
