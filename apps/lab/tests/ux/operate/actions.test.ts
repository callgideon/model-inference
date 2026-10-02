// UX-03 operate server actions, run for real through the renderer's seams (render.ts: the guard reads
// the fixture access; the control port is the labelled preview fake). The workspace and role are the
// session's, never the form's; a role without the capability is refused before the service is asked;
// invalid values come back with the user's input and never reach the service; an unanswered call is
// "not confirmed", never a refusal or a success.
import assert from "node:assert/strict";
import test from "node:test";
import { load, type Role } from "./render.ts";

Object.assign(process.env, { LAB_CONTROL_PREVIEW: "1" });
type Fake = import("../../../lib/services/control/fake.ts").FakeControl;
const { controlPort } = await load<typeof import("../../../lib/services/control/port.ts")>("lib/services/control/port.ts");
const fake = controlPort() as Fake;
const models = await load<typeof import("../../../app/(provider)/models/actions.ts")>("app/(provider)/models/actions.ts");

const P = "33333333-3333-4333-8333-333333333333";
const DIGEST = `sha256:${"c".repeat(64)}`;
fake.importModel(P, "synthetic/gamma-2b", [DIGEST]);
const as = (role: Role, providerId = P) => {
  const workspace = { providerId, providerName: "Synthetic Lab", role };
  (globalThis as { __operateAccess?: unknown }).__operateAccess = { kind: "ready", workspace, workspaces: [workspace] };
};
const form = (fields: Record<string, string>) => {
  const data = new FormData();
  for (const [k, value] of Object.entries(fields)) data.set(k, value);
  return data;
};
const REG = { name: "gamma-2b", artifactDigest: DIGEST, schemaVersion: "chat.v1", runtime: "vllm@sha256:cc" };
const calls = () => fake.calls.filter((c) => c[0] === "register").length;

test("OP-A01 a revision registers as the session's workspace and role, whatever the form claims, and returns the record", async () => {
  as("developer");
  const state = await models.registerRevision(null, form({ ...REG, providerId: "44444444-4444-4444-8444-444444444444", role: "administrator" }));
  assert.equal(state?.outcome?.kind, "registered");
  const [, actor] = fake.calls.at(-1)!;
  assert.deepEqual(actor, { providerId: P, providerName: "Synthetic Lab", role: "developer" });
  assert.deepEqual(state?.values, REG);
});

test("OP-A02 a viewer is refused before the control service is asked; invalid values come back with the input and are never sent", async () => {
  as("viewer");
  const before = calls();
  const denied = await models.registerRevision(null, form(REG));
  assert.deepEqual(denied?.outcome, { kind: "refused", message: "Your role in this workspace does not allow that." });
  as("developer");
  const bad = { ...REG, artifactDigest: "sha256:short" };
  const invalid = await models.registerRevision(null, form(bad));
  assert.equal(invalid?.outcome, null);
  assert.ok(invalid?.errors.artifactDigest);
  assert.deepEqual(invalid?.values, bad, "the user's input comes back as typed");
  assert.equal(calls(), before, "nothing reached the control service");
  (globalThis as { __operateAccess?: unknown }).__operateAccess = { kind: "denied" };
  await assert.rejects(models.registerRevision(null, form(REG)), /NEXT_NOT_FOUND/);
  assert.equal(calls(), before);
});

test("OP-A03 an unanswered registration is not confirmed; the service's refusal is its fixed reason", async () => {
  as("developer");
  const register = fake.register.bind(fake);
  try {
    fake.register = async () => ({ ok: false, reason: "unavailable" });
    const unsure = await models.registerRevision(null, form(REG));
    assert.equal(unsure?.outcome?.kind, "uncertain");
    fake.register = async () => ({ ok: false, reason: "not_found" });
    const refused = await models.registerRevision(null, form(REG));
    assert.equal(refused?.outcome?.kind, "refused");
  } finally {
    fake.register = register;
  }
});
