// L4 server actions, run for real (module hooks as tests/l/shell/guard.test.ts): the provider and role
// come from the session, never the form; refusals are a fixed reason in the URL; success is the
// records themselves (a plain redirect back), never an action-local "done".
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import type { FakeControl } from "../../../lib/services/control/fake.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "administrator" };
type World = { rows: unknown[] };
const world: World = ((globalThis as unknown as { labUi: World }).labUi = { rows: [A] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    return { getAll: () => [], get: () => undefined, set: () => {} };
  }`,
  "@supabase/ssr": `export function createServerClient() {
    return { auth: { getUser: async () => ({ data: { user: { id: "u1" } } }) },
             rpc: async () => ({ data: globalThis.labUi.rows, error: null }) };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
Object.assign(process.env, { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_CONTROL_PREVIEW: "1" });
const { registerModel, smokeDeployment, proposeChange } = await import("../../../lib/services/control/actions.ts");
const { controlPort } = await import("../../../lib/services/control/port.ts");
const control = controlPort() as FakeControl;

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
const REG = { name: "acme-7b", artifactDigest: `sha256:${"a".repeat(64)}`, schemaVersion: "chat.v2", runtime: "vllm@sha256:bb" };
const as = (role: string, provider = A.provider_org_id) => (world.rows = [{ ...A, role, provider_org_id: provider }]);

test("L4-A01 register runs as the session's provider and role, whatever the form claims", async () => {
  as("developer");
  const before = control.calls.length;
  assert.equal(await landing(registerModel(form({ ...REG, providerId: "22222222-2222-4222-8222-222222222222", role: "administrator" }))), "/models");
  assert.deepEqual(control.calls.slice(before), [["register", { providerId: A.provider_org_id, role: "developer" }, REG]]);
});

test("L4-A02 a role without the capability is refused before the control service is asked", async () => {
  as("viewer");
  const before = control.calls.length;
  assert.equal(await landing(registerModel(form(REG))), "/models?refused=denied");
  assert.equal(await landing(smokeDeployment(form({ deploymentRevisionId: "d" }))), "/deployments?refused=denied");
  as("developer");
  assert.equal(await landing(proposeChange(form({ kind: "publish", deploymentRevisionId: "d" }))), "/deployments?refused=denied");
  assert.equal(control.calls.length, before);
});

test("L4-A03 malformed input is refused as invalid and never reaches the control service", async () => {
  as("administrator");
  const before = control.calls.length;
  for (const bad of [{ ...REG, artifactDigest: "latest" }, { ...REG, name: "Acme 7B!" }, { ...REG, runtime: "" }, { name: "x" }])
    assert.equal(await landing(registerModel(form(bad))), "/models?refused=invalid");
  assert.equal(await landing(proposeChange(form({ kind: "delete", deploymentRevisionId: "d" }))), "/deployments?refused=invalid");
  assert.equal(await landing(smokeDeployment(form({}))), "/deployments?refused=invalid");
  assert.equal(control.calls.length, before);
});

test("L4-A04 the service's refusal is carried as its reason; its success is a plain return to the records", async () => {
  as("administrator");
  assert.equal(await landing(smokeDeployment(form({ deploymentRevisionId: "nope" }))), "/deployments?refused=not_found");
  assert.equal(await landing(registerModel(form({ ...REG, name: "beta-1b" }))), "/models");
  const mine = await control.deployments({ providerId: A.provider_org_id, role: "administrator" });
  assert.ok(mine.ok);
  const id = mine.value.find((x) => x.modelId === "beta-1b")!.deploymentRevisionId;
  assert.equal(await landing(proposeChange(form({ kind: "publish", deploymentRevisionId: id }))), "/deployments?refused=conflict");
  assert.equal(await landing(smokeDeployment(form({ deploymentRevisionId: id }))), "/deployments");
  assert.equal(await landing(proposeChange(form({ kind: "publish", deploymentRevisionId: id }))), "/deployments");
});

test("L4-A05 a consumer-only user gets a 404 from every action and the control service is never asked", async () => {
  world.rows = [];
  const before = control.calls.length;
  for (const run of [registerModel(form(REG)), smokeDeployment(form({ deploymentRevisionId: "d" })), proposeChange(form({ kind: "publish", deploymentRevisionId: "d" }))])
    assert.equal(await landing(run), "404:true");
  assert.equal(control.calls.length, before);
});
