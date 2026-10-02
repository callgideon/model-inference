// UX-03 operate pages (UX-T07 roles, UX-T08 registration, UX-T09 partial services), the real pages in
// the real provider layout (render.ts) over the labelled preview fake (LAB_CONTROL_PREVIEW=1) seeded
// with synthetic records, laid out in Chromium at 390/768/1440 and walked by keyboard.
import assert from "node:assert/strict";
import test from "node:test";
import { kit, load, mainOf, route, text, type Membership, type Role } from "./render.ts";

Object.assign(process.env, { LAB_CONTROL_PREVIEW: "1" });
type Port = typeof import("../../../lib/services/control/port.ts");
type Fake = import("../../../lib/services/control/fake.ts").FakeControl;
const { controlPort } = await load<Port>("lib/services/control/port.ts");
const fake = controlPort() as Fake;
const real = { aggregates: fake.aggregates.bind(fake), proposals: fake.proposals.bind(fake), deployments: fake.deployments.bind(fake), models: fake.models.bind(fake) };
const restore = () => Object.assign(fake, real);
const DOWN = async () => ({ ok: false as const, reason: "unavailable" as const });

const P = "11111111-1111-4111-8111-111111111111";
const EMPTY = "22222222-2222-4222-8222-222222222222";
const DIGEST = `sha256:${"a".repeat(64)}`;
fake.importModel(P, "synthetic/alpha-2b", [DIGEST]);
fake.observe(P, { deploymentRevisionId: "d-synthetic", windowStart: "2026-09-30T09:00:00Z", windowEnd: "2026-09-30T10:00:00Z", requests: 0, errors: 0, p95LatencyMs: null });

const as = (role: Role, providerId = P): Membership => ({ providerId, providerName: "Synthetic Lab", role });
const at = (file: string, path: string, role: Role, providerId = P, query: Record<string, string> = {}) => {
  const workspace = as(role, providerId);
  return route(file, { kind: "ready", workspace, workspaces: [workspace] }, path, query);
};

const { open, laidOut, close } = await kit();
test.after(close);

test("OP-P01 overview: an empty workspace has one obvious next action; a viewer is told why it has none", async () => {
  const dev = await at("overview/page.tsx", "/overview", "developer", EMPTY);
  assert.equal(dev.split('href="/models/new"').length - 1, 1, "one Add model action");
  assert.match(dev, />Add model</);
  assert.match(dev, /<h1[^>]*>Overview<\/h1>/);
  const p = await open(dev);
  const shown = await text(p);
  for (const stage of ["Add a model", "Verify a private deployment", "Request publication"]) assert.ok(shown.includes(stage), stage);
  assert.match(shown, /Serving readiness is not available in this view/);
  await p.close();
  const viewer = await at("overview/page.tsx", "/overview", "viewer", EMPTY);
  assert.doesNotMatch(viewer, /href="\/models\/new"/);
  assert.match(viewer, /needs a developer or administrator/);
  await laidOut(dev);
});

test("OP-P02 overview: control records and measured traffic load and fail apart; neither failure becomes a zero", async () => {
  try {
    fake.aggregates = DOWN;
    const traffic = await at("overview/page.tsx", "/overview", "administrator");
    let p = await open(traffic);
    let shown = await text(p);
    assert.match(shown, /We couldn't load measured traffic/);
    assert.match(shown, /Registered deployment records [1-9]/, `the control counts survive: ${shown}`);
    assert.doesNotMatch(shown, /No measured requests/);
    await p.close();
    restore();
    fake.deployments = DOWN;
    fake.proposals = DOWN;
    fake.models = DOWN;
    const control = await at("overview/page.tsx", "/overview", "administrator");
    p = await open(control);
    shown = await text(p);
    assert.match(shown, /We couldn't load control records/);
    assert.match(shown, /Couldn't check/, "the setup stages say unknown");
    assert.doesNotMatch(shown, /Registered deployment records 0|Public records 0/);
    assert.match(shown, /No measured requests/, "the traffic section still renders its own records");
    assert.match(shown, /Not available/);
    assert.match(shown, /Observed through/);
    assert.match(shown, /Last loaded/);
    await p.close();
  } finally {
    restore();
  }
});

test("OP-P03 models: a developer gets Add model and the revision form for imported models only; a viewer gets neither; an empty workspace is pointed at Add model", async () => {
  const dev = await at("models/page.tsx", "/models", "developer");
  assert.match(dev, /href="\/models\/new"[^>]*>Add model</);
  assert.match(dev, /New revision of an imported model/);
  assert.match(dev, /<legend>Identify<\/legend>[\s\S]*<legend>Revision<\/legend>/);
  assert.match(dev, /<option value="alpha-2b"[^>]*>alpha-2b<\/option>/, "the imported model is the name");
  assert.doesNotMatch(dev, /upload weights|Live|Healthy/i);
  const p = await open(dev);
  const shown = await text(p);
  assert.match(shown, /alpha-2b r1/);
  await p.close();
  const viewer = await at("models/page.tsx", "/models", "viewer");
  assert.doesNotMatch(viewer, /href="\/models\/new"|<legend>Identify/);
  assert.match(viewer, /needs a developer or administrator/);
  const empty = await at("models/page.tsx", "/models", "developer", EMPTY);
  assert.doesNotMatch(empty, /<legend>Identify/, "the legacy form cannot succeed without an imported model");
  assert.match(empty, /No models are registered in this workspace yet/);
  assert.match(empty, /Add model/);
  await laidOut(dev);
});

test("OP-P04 the revision fields keep the user's values and tie each error to its field", async () => {
  type Fields = { RevisionFields: (p: Record<string, unknown>) => import("react").ReactElement };
  const { RevisionFields } = await load<Fields>("app/(provider)/models/revision-form.tsx");
  const { html } = await import("./render.ts");
  const values = { name: "alpha-2b", artifactDigest: "sha256:short", schemaVersion: "chat.v1", runtime: "vllm@sha256:bb" };
  const markup = await html(RevisionFields({ names: ["alpha-2b"], values, errors: { artifactDigest: "Enter the artifact digest as sha256: followed by 64 lowercase hex characters." } }));
  assert.match(markup, /value="sha256:short"/);
  assert.match(markup, /aria-invalid="true"/);
  assert.match(markup, /sha256: followed by 64/);
  assert.equal(markup.split('aria-invalid="true"').length - 1, 1, "only the bad field is marked");
});

// ---- AP-09 09d: the Add model wizard over a fixture AP-04 port (render.ts "./api").
const answers = new Map<string, unknown>();
const ok = (data: unknown) => ({ ok: true, status: 200, data, requestId: "r", location: null });
const G = globalThis as { __operateArtifacts?: unknown };
const port = { call: async (_a: unknown, method: string, path: string, init: { params?: Record<string, string> } = {}) =>
  answers.get(`${method} ${path} ${JSON.stringify(init.params ?? {})}`) ?? { ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } } };
const PROJECT = { project_id: "proj-1", slug: "alpha", name: "Synthetic Alpha", description: "", created_at: "2026-10-01T10:00:00Z" };
const wizardAt = (role: Role, query: Record<string, string>) => at("models/new/page.tsx", "/models/new", role, P, query);
answers.set(`get /lab/v1/control/model-projects {}`, ok({ data: [PROJECT], next_cursor: null }));

test("OP-P05 add model: without the import service, or for a viewer, no form is offered - only the actual prerequisite", async () => {
  G.__operateArtifacts = null;
  const off = await wizardAt("developer", {});
  assert.match(off, /data-state="unavailable"/);
  assert.match(off, /Model import isn't available here yet/);
  assert.doesNotMatch(mainOf(off), /<form/i, "no form that cannot succeed");
  G.__operateArtifacts = port;
  const viewer = await wizardAt("viewer", {});
  assert.match(viewer, /data-state="denied"/);
  assert.doesNotMatch(mainOf(viewer), /<form/i);
});

test("OP-P06 add model: a project form with a stable operation key, then repository import and browser upload for that project", async () => {
  G.__operateArtifacts = port;
  const first = await wizardAt("developer", {});
  assert.match(first, /aria-current="step"[^>]*>[^<]*Model project/);
  const keys = [...first.matchAll(/name="idempotencyKey" value="([0-9a-f-]{36})"/g)].map((m) => m[1]);
  assert.equal(keys.length, 1, "one key per rendered form");
  assert.match(first, /href="\/models\/new\?project=proj-1"[^>]*>[^<]*Synthetic Alpha/, "an existing project can be continued");
  const source = await wizardAt("developer", { project: "proj-1" });
  assert.match(source, /aria-current="step"[^>]*>[^<]*Repository or upload/);
  assert.match(source, /<option value="huggingface.co"/);
  assert.match(source, /name="commit"/);
  assert.match(source, /never paste a token/i);
  assert.match(source, /type="file"/);
  assert.match(source, /name="projectId" value="proj-1"/);
  const gone = await wizardAt("developer", { project: "proj-404" });
  assert.match(gone, /data-state="not_found"/);
  assert.doesNotMatch(gone, /name="commit"/);
  await laidOut(source, 14);
});

test("OP-P07 add model: verification shows the operation's own state; only a succeeded one naming its artifact reads as verified", async () => {
  G.__operateArtifacts = port;
  const op = (over: Record<string, unknown>) => ok({ operation_id: "op-1", kind: "artifact.import", state: "running", phase: "hashing", resource_id: null, created_at: "t", updated_at: "2026-10-01T10:05:00Z", retry_after_s: 3, error: null, ...over });
  answers.set(`get /lab/v1/operations/{operation_id} {"operation_id":"op-1"}`, op({}));
  const running = await wizardAt("developer", { project: "proj-1", operation: "op-1" });
  assert.match(running, /Verifying/);
  assert.match(running, /Check again/);
  assert.doesNotMatch(running, /Artifact verified|%/);
  answers.set(`get /lab/v1/operations/{operation_id} {"operation_id":"op-1"}`, op({ state: "succeeded", resource_id: "art-1" }));
  const done = await wizardAt("developer", { project: "proj-1", operation: "op-1" });
  assert.match(done, /Artifact verified/);
  assert.match(done, /href="\/models\/new\?project=proj-1&amp;artifact=art-1"/);
  assert.doesNotMatch(done, /Check again/);
  answers.set(`get /lab/v1/operations/{operation_id} {"operation_id":"op-1"}`, op({ state: "failed", error: { code: "missing_file", message: "model.safetensors was not received", request_id: "r", retryable: false } }));
  const failed = await wizardAt("developer", { project: "proj-1", operation: "op-1" });
  assert.match(failed, /Verification failed/);
  assert.match(failed, /model.safetensors was not received/);
  assert.doesNotMatch(failed, /Artifact verified/);
  answers.delete(`get /lab/v1/operations/{operation_id} {"operation_id":"op-1"}`);
  const lost = await wizardAt("developer", { project: "proj-1", operation: "op-1" });
  assert.match(lost, /data-state="unavailable"/);
  assert.doesNotMatch(lost, /Verification failed|Artifact verified/, "an unread operation is neither outcome");
  assert.match(lost, /href="\/models\/new\?project=proj-1&amp;operation=op-1"[^>]*>Try again</, "Try again re-reads the same step, naming only the ids it has");
});

test("OP-P08 add model: serving setup only for a compatible verified artifact; a created revision is not a deployment and no readiness is claimed", async () => {
  G.__operateArtifacts = port;
  const artifact = (compatibility: unknown) => ok({ artifact_id: "art-1", project_id: "proj-1", source: "import", source_repo: "org/model", source_commit: "a".repeat(40), files: [{ relative_path: "config.json", bytes: 1, sha256: `sha256:${"b".repeat(64)}`, media_type: "application/json" }], manifest_sha256: `sha256:${"c".repeat(64)}`, verified_at: "2026-10-01T10:06:00Z", compatibility });
  answers.set(`get /lab/v1/artifacts/{artifact_id} {"artifact_id":"art-1"}`, artifact({ supported: true, profile: "marlin2b.l40s.vllm", reasons: [] }));
  const setup = await wizardAt("developer", { project: "proj-1", artifact: "art-1" });
  assert.match(setup, /Create serving revision/);
  assert.match(setup, /marlin2b.l40s.vllm/);
  answers.set(`get /lab/v1/artifacts/{artifact_id} {"artifact_id":"art-1"}`, artifact({ supported: false, profile: "marlin2b.l40s.vllm", reasons: [{ field: "architecture", code: "unsupported", message: "not the supported Marlin architecture" }] }));
  const unsupported = await wizardAt("developer", { project: "proj-1", artifact: "art-1" });
  assert.match(unsupported, /not the supported Marlin architecture/);
  assert.doesNotMatch(unsupported, /Create serving revision/);
  answers.set(`get /lab/v1/control/model-projects/{project_id}/revisions {"project_id":"proj-1"}`, ok({ data: [{ serving_version_id: "sv-1", project_id: "proj-1", artifact_id: "art-1", public_model_id: "synthetic/alpha", revision_label: "r1", model_revision: "m1", created_at: "t" }], next_cursor: null }));
  const created = await wizardAt("developer", { project: "proj-1", artifact: "art-1", revision: "sv-1" });
  assert.match(created, /synthetic\/alpha/);
  assert.match(created, /Serving readiness can't be verified here yet/);
  assert.doesNotMatch(mainOf(created), /Deployed|Ready|Live|Healthy|Connect/);
  await laidOut(created);
});

// ---- UX-03 L-04 deployments over the preview fake: a dev revision registered and smoke-recorded.
const devReady = await (async () => {
  const d = await fake.register(as("developer"), { name: "alpha-2b", artifactDigest: DIGEST, schemaVersion: "chat.v1", runtime: "vllm@sha256:bb" });
  assert.ok(d.ok);
  await fake.smoke(as("developer"), d.value.deploymentRevisionId);
  return d.value.deploymentRevisionId;
})();

test("OP-P09 deployments: records read as registered; no dev smoke is offered; only an administrator can request publication, with a confirmation summary", async () => {
  const dev = mainOf(await at("deployments/page.tsx", "/deployments", "developer"));
  assert.match(dev, /Registered · active record/);
  assert.match(dev, /Recorded smoke result: passed/);
  assert.doesNotMatch(dev, /Run dev smoke|Request publication</);
  assert.match(dev, /Requesting publication needs an administrator/);
  assert.match(dev, /Connection details are provided after serving setup is verified/);
  assert.doesNotMatch(dev, /Healthy|Ready|Live\b|Published/);
  const admin = mainOf(await at("deployments/page.tsx", "/deployments", "administrator"));
  assert.match(admin, /<summary[^>]*>Request publication<\/summary>/);
  assert.match(admin, new RegExp(`name="deploymentRevisionId" value="${devReady}"`));
  assert.match(admin, /name="kind" value="publish"/);
  assert.match(admin, /An infrx operator approves or rejects it/);
  assert.match(admin, /Serving readiness: not verified here/);
  assert.match(admin, />Confirm publication request</);
  await laidOut(await at("deployments/page.tsx", "/deployments", "administrator"), 16);
});

test("OP-P10 deployments: a failed proposal read keeps the records and withholds the request; a failed record read is unavailable, never empty", async () => {
  try {
    fake.proposals = DOWN;
    const partial = mainOf(await at("deployments/page.tsx", "/deployments", "administrator"));
    assert.match(partial, /Registered · active record/);
    assert.match(partial, /Couldn't check/);
    assert.match(partial, /We couldn't load publication requests/);
    assert.doesNotMatch(partial, /Request publication<\/summary>/, "no request without knowing what is pending");
    restore();
    fake.deployments = DOWN;
    const down = mainOf(await at("deployments/page.tsx", "/deployments", "administrator"));
    assert.match(down, /data-state="unavailable"/);
    assert.match(down, /We couldn't load deployments/);
    assert.doesNotMatch(down, /No deployments yet/);
  } finally {
    restore();
  }
  const empty = mainOf(await at("deployments/page.tsx", "/deployments", "developer", EMPTY));
  assert.match(empty, /No deployments yet/);
  assert.match(empty, /href="\/models\/new"/);
});

test("OP-P11 settings: the workspace, the role in words with what it allows, customer content apart from every role, services not verified here; no invented controls", async () => {
  const dev = mainOf(await at("settings/page.tsx", "/settings", "developer"));
  assert.match(dev, /Synthetic Lab/);
  assert.match(dev, /Developer/);
  assert.match(dev, /registers models/i);
  const p = await open(await at("settings/page.tsx", "/settings", "developer"));
  const shown = await text(p);
  assert.match(shown, /Register models and manage private deployments Yes/);
  assert.match(shown, /Request publication No/);
  assert.match(shown, /Read customer content Not part of any role/);
  for (const service of ["Control records", "Requests", "Model import", "Serving readiness"]) assert.match(shown, new RegExp(`${service} Not yet verified here`));
  await p.close();
  assert.doesNotMatch(dev, /<form|Invite|API key|CREDIT|quota/i, "settings has no controls of its own");
  assert.equal((dev.match(/<button/g) ?? []).length, 1, "only the workspace id's copy button");
  const admin = await (async () => { const q = await open(await at("settings/page.tsx", "/settings", "administrator")); const t = await text(q); await q.close(); return t; })();
  assert.match(admin, /Request publication Yes/);
  await laidOut(await at("settings/page.tsx", "/settings", "viewer"));
});
