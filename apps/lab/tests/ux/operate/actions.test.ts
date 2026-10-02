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

// ---- AP-09 09d: the Add model wizard's actions over a recording AP-04 port (render.ts "./api").
type Call = { actor: unknown; method: string; path: string; init: { body?: unknown; idempotencyKey?: string; params?: Record<string, string> } };
const sent: Call[] = [];
const replies = new Map<string, unknown>();
const NO_ANSWER = { ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } };
(globalThis as { __operateArtifacts?: unknown }).__operateArtifacts = {
  call: async (actor: unknown, method: string, path: string, init: Call["init"] = {}) => {
    sent.push({ actor, method, path, init });
    return replies.get(`${method} ${path}`) ?? NO_ANSWER;
  },
};
const reply = (key: string, data: unknown, status = 201) => replies.set(key, { ok: true, status, data, requestId: "r", location: null });
const wizard = await load<typeof import("../../../app/(provider)/models/new/actions.ts")>("app/(provider)/models/new/actions.ts");
const KEY = "0f0e0d0c-0b0a-4908-8706-050403020100";
const redirected = (run: Promise<unknown>) => run.then((v) => ({ value: v }), (e: Error) => ({ to: /^NEXT_REDIRECT (.*)$/.exec(e.message)?.[1] ?? e.message }));
const FILE = { relative_path: "config.json", bytes: 12, sha256: `sha256:${"b".repeat(64)}`, media_type: "application/json" };

test("OP-A04 a model project is created as the session's workspace with the form's key; a viewer or a stale form is refused before the API is asked", async () => {
  sent.length = 0;
  as("viewer");
  const denied = await wizard.createProject(null, form({ name: "Alpha", slug: "alpha", idempotencyKey: KEY }));
  assert.match(denied?.message ?? "", /does not allow/);
  as("developer");
  const stale = await wizard.createProject(null, form({ name: "Alpha", slug: "alpha", idempotencyKey: "not-a-key" }));
  assert.match(stale?.message ?? "", /Reload the page/);
  assert.equal(sent.length, 0, "nothing reached the API");
  reply("post /lab/v1/control/model-projects", { project_id: "proj-1", slug: "alpha", name: "Alpha", description: "", created_at: "t" });
  const made = await redirected(wizard.createProject(null, form({ name: "Alpha", slug: "alpha", description: "SOP video", idempotencyKey: KEY, providerId: "x" })));
  assert.deepEqual(made, { to: "/models/new?project=proj-1" });
  assert.deepEqual(sent.at(-1), {
    actor: { providerId: P, providerName: "Synthetic Lab", role: "developer" }, method: "post", path: "/lab/v1/control/model-projects",
    init: { body: { name: "Alpha", slug: "alpha", description: "SOP video" }, idempotencyKey: KEY },
  });
  replies.clear();
  const unsure = await wizard.createProject(null, form({ name: "Alpha", slug: "alpha", idempotencyKey: KEY }));
  assert.match(unsure?.message ?? "", /Outcome not confirmed/);
  assert.deepEqual(unsure?.values, { name: "Alpha", slug: "alpha", description: "" }, "the input comes back");
});

test("OP-A05 an import sends the pinned source, a secret reference and the manifest; a bad manifest stays in the form and is never sent; the reference is never echoed", async () => {
  as("developer");
  sent.length = 0;
  const fields = { projectId: "proj-1", host: "huggingface.co", repo: "org/model", commit: "a".repeat(40), secretRef: "ssm:/model-inference/hf_token", idempotencyKey: KEY };
  const bad = await wizard.startImport(null, form({ ...fields, manifest: "[{" }));
  assert.match(bad?.message ?? "", /not valid JSON/);
  assert.equal(bad?.values.manifest, "[{");
  assert.equal(bad?.values.secretRef, undefined, "a secret reference field is never sent back");
  assert.equal(sent.length, 0);
  replies.set("post /lab/v1/artifacts/imports", { ok: false, requestId: "r", error: { kind: "error", status: 422, code: "invalid_request", message: "m", requestId: "r", retryable: false, operationId: null, resourceId: null, fieldErrors: [{ field: "source.commit", code: "value_error", message: "mutable_ref" }] } });
  const refused = await wizard.startImport(null, form({ ...fields, manifest: JSON.stringify([FILE]) }));
  assert.deepEqual(refused?.fields, ["source.commit: mutable_ref"]);
  assert.deepEqual(sent.at(-1)?.init.body, { project_id: "proj-1", source: { host: "huggingface.co", repo: "org/model", commit: "a".repeat(40) }, secret_ref: "ssm:/model-inference/hf_token", files: [FILE] });
  reply("post /lab/v1/artifacts/imports", { operation_id: "op-7", kind: "artifact.import", state: "queued", created_at: "t", updated_at: "t" }, 202);
  assert.deepEqual(await redirected(wizard.startImport(null, form({ ...fields, secretRef: "", manifest: JSON.stringify([FILE]) }))), { to: "/models/new?project=proj-1&operation=op-7" });
  assert.equal((sent.at(-1)?.init.body as { secret_ref: unknown }).secret_ref, null, "no reference: none is sent");
  replies.clear();
});

test("OP-A06 the browser upload's three calls refuse a viewer and malformed arguments, and pass the session's workspace and key", async () => {
  sent.length = 0;
  as("viewer");
  assert.equal((await wizard.createUpload("proj-1", [FILE], KEY)).ok, false);
  as("developer");
  for (const args of [["../p", [FILE], KEY], ["proj-1", "files", KEY], ["proj-1", [FILE], "k"], ["proj-1", [], KEY]] as const)
    assert.equal((await wizard.createUpload(...(args as unknown as [string, never[], string]))).ok, false, JSON.stringify(args));
  assert.equal((await wizard.grantPart("u/1", "config.json")).ok, false);
  assert.equal((await wizard.completeUpload("up-1", "sha256:x", KEY)).ok, false);
  assert.equal(sent.length, 0, "nothing malformed reached the API");
  reply("post /lab/v1/artifacts/uploads", { upload_id: "up-1", project_id: "proj-1", manifest_sha256: `sha256:${"c".repeat(64)}`, state: "open", expires_at: "t" });
  reply("post /lab/v1/artifacts/uploads/{upload_id}/parts", { relative_path: "config.json", url: "http://objects.test/put", expires_s: 300 }, 200);
  reply("post /lab/v1/artifacts/uploads/{upload_id}/complete", { operation_id: "op-8", kind: "artifact.upload", state: "queued", created_at: "t", updated_at: "t" }, 202);
  assert.deepEqual(await wizard.createUpload("proj-1", [FILE], KEY), { ok: true, uploadId: "up-1", manifestSha: `sha256:${"c".repeat(64)}` });
  assert.deepEqual(await wizard.grantPart("up-1", "config.json"), { ok: true, url: "http://objects.test/put" });
  assert.deepEqual(await wizard.completeUpload("up-1", `sha256:${"c".repeat(64)}`, KEY), { ok: true, operationId: "op-8" });
  assert.deepEqual(sent.map((c) => [c.path, c.init.idempotencyKey, c.init.params]), [
    ["/lab/v1/artifacts/uploads", KEY, undefined],
    ["/lab/v1/artifacts/uploads/{upload_id}/parts", undefined, { upload_id: "up-1" }],
    ["/lab/v1/artifacts/uploads/{upload_id}/complete", KEY, { upload_id: "up-1" }],
  ]);
  replies.clear();
});

test("OP-A07 a serving revision is created for the verified artifact; an unsupported one comes back with the server's reasons", async () => {
  as("developer");
  replies.set("post /lab/v1/control/model-projects/{project_id}/revisions", { ok: false, requestId: "r", error: { kind: "error", status: 422, code: "invalid_request", message: "m", requestId: "r", retryable: false, operationId: null, resourceId: null, fieldErrors: [{ field: "architecture", code: "unsupported", message: "not the supported Marlin architecture" }] } });
  const refused = await wizard.createRevision(null, form({ projectId: "proj-1", artifactId: "art-1", idempotencyKey: KEY }));
  assert.deepEqual(refused?.fields, ["architecture: not the supported Marlin architecture"]);
  reply("post /lab/v1/control/model-projects/{project_id}/revisions", { serving_version_id: "sv-1", project_id: "proj-1", artifact_id: "art-1", public_model_id: "synthetic/alpha", revision_label: "r1", model_revision: "m", created_at: "t" });
  assert.deepEqual(await redirected(wizard.createRevision(null, form({ projectId: "proj-1", artifactId: "art-1", idempotencyKey: KEY }))), { to: "/models/new?project=proj-1&artifact=art-1&revision=sv-1" });
  assert.deepEqual([sent.at(-1)?.init.params, sent.at(-1)?.init.body], [{ project_id: "proj-1" }, { artifact_id: "art-1" }]);
  as("viewer");
  assert.match((await wizard.createRevision(null, form({ projectId: "proj-1", artifactId: "art-1", idempotencyKey: KEY })))?.message ?? "", /does not allow/);
  replies.clear();
});
