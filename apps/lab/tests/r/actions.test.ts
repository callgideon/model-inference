// R4 server actions, run for real (module hooks as tests/l/ui/actions.test.ts): provider and role come
// from the session, never the form; refusals are a fixed reason in the URL; success is a plain return
// to the records (the page shows the pending request, never an action-local "done").
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import type { FakeReleases } from "../../lib/services/rollouts/fake.ts";
import { A as PROVIDER, EXPAND, POLICY, release } from "./fixtures.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
const A = { provider_org_id: PROVIDER, provider_name: "Acme", role: "administrator" };
type World = { rows: unknown[] };
const world: World = ((globalThis as unknown as { labReleases: World }).labReleases = { rows: [A] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    return { getAll: () => [], get: () => undefined, set: () => {} };
  }`,
  "@supabase/ssr": `export function createServerClient() {
    return { auth: { getUser: async () => ({ data: { user: { id: "u1" } } }) },
             rpc: async () => ({ data: globalThis.labReleases.rows, error: null }) };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
Object.assign(process.env, { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_RELEASES_PREVIEW: "1" });
const { proposeRelease } = await import("../../lib/services/rollouts/actions.ts");
const { releasesPort } = await import("../../lib/services/rollouts/port.ts");
const fake = releasesPort() as FakeReleases;
fake.seed(PROVIDER, release({ verdict: EXPAND }));

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
const as = (role: string, provider = A.provider_org_id) => (world.rows = [{ ...A, role, provider_org_id: provider }]);
const OK = { kind: "rollback", policyRef: POLICY, fence: "3" };

test("R4-A01 a proposal runs as the session's provider and role, whatever the form claims", async () => {
  as("administrator");
  const before = fake.calls.length;
  const claims = { ...OK, kind: "expand", providerId: "22222222-2222-4222-8222-222222222222", role: "operator" };
  assert.equal(await landing(proposeRelease(form(claims))), "/releases");
  assert.deepEqual(fake.calls.slice(before), [["propose", { providerId: PROVIDER, providerName: "Acme", role: "administrator" }, "expand", POLICY, 3]]);
});

test("R4-A02 a role without propose_publication is refused before the releases service is asked", async () => {
  const before = fake.calls.length;
  for (const role of ["viewer", "developer"]) {
    as(role);
    assert.equal(await landing(proposeRelease(form(OK))), "/releases?refused=denied");
  }
  assert.equal(fake.calls.length, before);
});

test("R4-A03 malformed input is refused as invalid and never reaches the releases service", async () => {
  as("administrator");
  const before = fake.calls.length;
  for (const bad of [{ ...OK, kind: "launch" }, { ...OK, fence: "-1" }, { ...OK, fence: "3.5" }, { ...OK, fence: "" }, { ...OK, policyRef: "latest" },
    { ...OK, policyRef: POLICY.replace("lab:policy:", "lab:serving:") }, { kind: "rollback" }])
    assert.equal(await landing(proposeRelease(form(bad))), "/releases?refused=invalid", JSON.stringify(bad));
  assert.equal(fake.calls.length, before);
});

test("R4-A04 the service's refusal is carried as its reason; its success is a plain return to the records", async () => {
  as("administrator");
  assert.equal(await landing(proposeRelease(form({ ...OK, fence: "9" }))), "/releases?refused=conflict");
  assert.equal(await landing(proposeRelease(form({ ...OK, policyRef: POLICY.replace(/3333/g, "aaaa") }))), "/releases?refused=not_found");
});

test("R4-A05 a consumer-only user gets a 404 and the releases service is never asked", async () => {
  world.rows = [];
  const before = fake.calls.length;
  assert.equal(await landing(proposeRelease(form(OK))), "404:true");
  assert.equal(fake.calls.length, before);
});
