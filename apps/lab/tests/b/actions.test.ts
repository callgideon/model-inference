// B4 server actions and the report export, run for real (module hooks as tests/l/ui/actions.test.ts):
// the actor is the session's, never the form's; the role and the shape are refused before the port is
// asked; a launch lands on the records it queued, never on an action-local "done".
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import type { FakeEvaluation } from "../../lib/services/evaluation/fake.ts";
import { REPORTS, same } from "./real.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
const P = "11111111-1111-4111-8111-111111111111";
const A = { provider_org_id: P, provider_name: "Acme", role: "developer" };
type World = { rows: unknown[] };
const world: World = ((globalThis as unknown as { labEval: World }).labEval = { rows: [A] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    return { getAll: () => [], get: () => undefined, set: () => {} };
  }`,
  "@supabase/ssr": `export function createServerClient() {
    return { auth: { getUser: async () => ({ data: { user: { id: "u1" } } }) },
             rpc: async () => ({ data: globalThis.labEval.rows, error: null }) };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
Object.assign(process.env, { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_EVALS_PREVIEW: "1" });
const { launchExperiment, cancelRun, subscribeCheckpoints } = await import("../../lib/services/evaluation/actions.ts");
const { GET } = await import("../../app/(provider)/experiments/[id]/report/route.ts");
const { evaluationPort } = await import("../../lib/services/evaluation/port.ts");
const port = evaluationPort() as FakeEvaluation;

const ref = (kind: string, n: number) => `lab:${kind}:${P}:0000000${n}-0000-4000-8000-00000000000${n}@sha256:${String(n).repeat(64)}`;
port.offer(P, {
  datasets: [{ ref: ref("dataset", 1), label: "d" }], harnesses: [{ ref: ref("harness", 2), harness_id: "h", version: 1, adapter: "text" }],
  servings: [{ ref: ref("serving", 3), label: "a" }, { ref: ref("serving", 4), label: "b" }], evaluators: [{ ref: ref("evaluator", 5), label: "e" }],
});
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
const as = (role: string, provider = P) => (world.rows = [{ ...A, role, provider_org_id: provider }]);
let n = 0;
const id = () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++n).padStart(12, "0")}`;
const LAUNCH = () => ({
  experiment_id: id(), dataset_ref: ref("dataset", 1), harness_ref: ref("harness", 2), evaluator_ref: ref("evaluator", 5),
  baseline_serving_ref: ref("serving", 3), candidate_serving_ref: ref("serving", 4), seed: "7", max_cases: "40", run_limit: "10",
  confidence: "0.95", margin: "0.05", min_cases: "10", metric_source: "deterministic_metric", slices: "safety 0 5\r\nlong-tail 0.1 8\n",
});
const SUB = () => ({
  subscription_id: id(), external_run_ref: ref("external_run", 6), dataset_ref: ref("dataset", 1), harness_ref: ref("harness", 2),
  evaluator_ref: ref("evaluator", 5), seed: "7", max_cases: "40", run_limit: "5", limit: "20.5", max_active: "2", policy: "latest_only",
});

test("B4-A01 a launch runs as the session's provider and role and sends the parsed, predeclared protocol", async () => {
  as("developer");
  const before = port.calls.length;
  const fields = LAUNCH();
  assert.equal(await landing(launchExperiment(form({ ...fields, providerId: "22222222-2222-4222-8222-222222222222", role: "administrator" }))), `/experiments/${fields.experiment_id}`);
  same(port.calls.slice(before), [["launch", { providerId: P, providerName: "Acme", role: "developer" }, {
    experiment_id: fields.experiment_id, dataset_ref: ref("dataset", 1), harness_ref: ref("harness", 2), evaluator_ref: ref("evaluator", 5),
    baseline_serving_ref: ref("serving", 3), candidate_serving_ref: ref("serving", 4), seed: 7, max_cases: 40,
    run_limit: { unit: "CREDIT", value: "10.00000000" },
    protocol: { confidence: 0.95, margin: 0.05, min_cases: 10, metric_source: "deterministic_metric",
      required_slices: { safety: { margin: 0, min_cases: 5 }, "long-tail": { margin: 0.1, min_cases: 8 } } },
  }]]);
  const sub = SUB();
  assert.equal(await landing(subscribeCheckpoints(form(sub))), "/evaluations/checkpoints");
  assert.deepEqual(port.calls.at(-1)!.slice(0, 2), ["subscribe", { providerId: P, providerName: "Acme", role: "developer" }]);
  assert.deepEqual((port.calls.at(-1)![2] as { limit: unknown; max_active: number }).limit, { unit: "CREDIT", value: "20.50000000" });
});

test("B4-A02 a viewer is refused before the evaluation service is asked", async () => {
  as("viewer");
  const before = port.calls.length;
  assert.equal(await landing(launchExperiment(form(LAUNCH()))), "/evaluations?refused=denied");
  assert.equal(await landing(cancelRun(form({ run_id: id() }))), "/evaluations?refused=denied");
  assert.equal(await landing(subscribeCheckpoints(form(SUB()))), "/evaluations/checkpoints?refused=denied");
  assert.equal(port.calls.length, before);
});

test("B4-A03 malformed launch, cancel and subscription input is refused as invalid and never reaches the service", async () => {
  as("administrator");
  const before = port.calls.length;
  const bad: Record<string, string>[] = [
    { dataset_ref: ref("harness", 2) }, { harness_ref: "latest" }, { candidate_serving_ref: `${ref("serving", 4)} ` }, { experiment_id: "1" },
    { seed: "-1" }, { seed: "1.5" }, { max_cases: "0" }, { run_limit: "-1" }, { run_limit: "1e3" }, { run_limit: "0.123456789" },
    { confidence: "1" }, { confidence: "0.5" }, { confidence: "x" }, { margin: "-0.1" }, { margin: "Infinity" }, { min_cases: "1" },
    { metric_source: "vibes" }, { slices: "safety 0" }, { slices: "safety 0 1" }, { slices: "safety -1 5" }, { slices: "safety 0 5 9" },
    { slices: "safety 0 5\nsafety 0 6" },
  ];
  for (const over of bad) assert.equal(await landing(launchExperiment(form({ ...LAUNCH(), ...over }))), "/evaluations?refused=invalid", JSON.stringify(over));
  for (const over of [{ policy: "sometimes" }, { max_active: "0" }, { external_run_ref: ref("dataset", 1) }, { limit: "" }])
    assert.equal(await landing(subscribeCheckpoints(form({ ...SUB(), ...over }))), "/evaluations/checkpoints?refused=invalid", JSON.stringify(over));
  assert.equal(await landing(cancelRun(form({ run_id: "../x" }))), "/evaluations?refused=invalid");
  assert.equal(port.calls.length, before);
});

test("B4-A04 the service's refusal is carried as its reason; a cancel lands back on the run records", async () => {
  as("developer");
  assert.equal(await landing(cancelRun(form({ run_id: id() }))), "/evaluations?refused=not_found");
  assert.equal(await landing(launchExperiment(form({ ...LAUNCH(), dataset_ref: ref("dataset", 7) }))), "/evaluations?refused=invalid");
  const fields = LAUNCH();
  await landing(launchExperiment(form(fields)));
  const e = await port.experiments({ providerId: P, role: "developer" });
  assert.ok(e.ok);
  const runId = e.value.find((x) => x.experiment_id === fields.experiment_id)!.candidate.run_id;
  assert.equal(await landing(cancelRun(form({ run_id: runId }))), "/evaluations");
  assert.equal(await landing(cancelRun(form({ run_id: runId }))), "/evaluations?refused=conflict");
});

test("B4-A05 a consumer-only user gets a 404 from every action and the export, and the service is never asked", async () => {
  world.rows = [];
  const before = port.calls.length;
  for (const run of [launchExperiment(form(LAUNCH())), cancelRun(form({ run_id: id() })), subscribeCheckpoints(form(SUB()))])
    assert.equal(await landing(run), "404:true");
  assert.equal(await landing(GET(new Request("http://lab/x"), { params: Promise.resolve({ id: id() }) })), "404:true");
  assert.equal(port.calls.length, before);
});

test("B4-A06 the export is the stored B2 report verbatim for the provider's own experiment, and nothing before it exists", async () => {
  as("viewer");
  const fields = LAUNCH();
  as("developer");
  await landing(launchExperiment(form(fields)));
  as("viewer");
  const get = (experiment: string) => GET(new Request("http://lab/x"), { params: Promise.resolve({ id: experiment }) });
  assert.equal((await get(fields.experiment_id)).status, 409);
  port.settle(fields.experiment_id, REPORTS.reject);
  const res = await get(fields.experiment_id);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-disposition"), `attachment; filename="experiment-${fields.experiment_id}.json"`);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  same(await res.json(), REPORTS.reject);
  assert.equal((await get(id())).status, 404);
  as("developer", "22222222-2222-4222-8222-222222222222");
  assert.equal((await get(fields.experiment_id)).status, 404);
});
