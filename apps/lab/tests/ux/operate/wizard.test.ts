// AP-09 09d: the Add model wizard's rules (models/new/wizard.ts) and its port onto AP-04
// (models/new/api.ts), with the port pinned to the checked OpenAPI artifact (consumer.json, where AP-04's
// operations are exported today). Oracles: only a validated id reaches a step; "Artifact verified" needs
// a succeeded operation naming its artifact; a pasted manifest is refused in place; an unanswered
// mutation is "not confirmed"; every call is the actor's workspace with the session's own token and the
// form's Idempotency-Key; the paths and fields the wizard reads exist in the artifact.
import assert from "node:assert/strict";
import { readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { LAB, load } from "./render.ts";

type Wizard = typeof import("../../../app/(provider)/models/new/wizard.ts");
type Api = typeof import("../../../app/(provider)/models/new/api.ts");
const w = await load<Wizard>("app/(provider)/models/new/wizard.ts");
const api = await load<Api>("app/(provider)/models/new/api.ts");
const op = (over: Record<string, unknown> = {}) => ({ operation_id: "op-1", kind: "artifact.import", state: "running", phase: "hashing", resource_id: null, created_at: "t", updated_at: "t", retry_after_s: 2, error: null, ...over }) as never;

test("OP-W01 the URL names a step only through validated ids, each after its project", () => {
  assert.equal(w.step(w.wizardQuery({})), "project");
  assert.equal(w.step(w.wizardQuery({ project: "p1" })), "source");
  assert.equal(w.step(w.wizardQuery({ project: "p1", operation: "op-1" })), "validation");
  assert.equal(w.step(w.wizardQuery({ project: "p1", operation: "op-1", artifact: "a1" })), "setup");
  assert.equal(w.step(w.wizardQuery({ project: "p1", artifact: "a1", revision: "sv1" })), "done");
  assert.equal(w.step(w.wizardQuery({ operation: "op-1", artifact: "a1" })), "project", "no step without its project");
  assert.deepEqual(w.wizardQuery({ project: "../x", operation: ["a", "b"], artifact: "a".repeat(65) }), { project: undefined, operation: undefined, artifact: undefined, revision: undefined });
});

test("OP-W02 an operation reads as its actual state: verified only when it succeeded and names its artifact", () => {
  for (const state of ["queued", "running"]) assert.deepEqual([w.operationView(op({ state })).label, w.operationView(op({ state })).terminal], ["Verifying", false]);
  assert.match(w.operationView(op()).detail, /\(hashing\)/);
  const done = w.operationView(op({ state: "succeeded", resource_id: "a1" }));
  assert.deepEqual([done.label, done.tone, done.artifactId, done.terminal], ["Artifact verified", "success", "a1", true]);
  const empty = w.operationView(op({ state: "succeeded" }));
  assert.notEqual(empty.label, "Artifact verified");
  assert.equal(empty.artifactId, null);
  const failed = w.operationView(op({ state: "failed", error: { code: "hash_mismatch", message: "config.json does not match its declared sha256", request_id: "r", retryable: false } }));
  assert.deepEqual([failed.label, failed.tone, failed.detail], ["Verification failed", "danger", "config.json does not match its declared sha256"]);
  assert.equal(w.operationView(op({ state: "cancel_requested" })).terminal, false, "a cancel request is not an outcome");
});

test("OP-W03 a pasted manifest is a non-empty list of whole file entries, or says what is wrong", () => {
  const file = { relative_path: "config.json", bytes: 12, sha256: `sha256:${"b".repeat(64)}`, media_type: "application/json" };
  assert.deepEqual(w.parseManifest(JSON.stringify([file])), { files: [file] });
  assert.deepEqual(w.parseManifest(JSON.stringify({ files: [{ ...file, extra: 1 }] })), { files: [file] }, "the {files} shape is accepted; unknown keys are dropped");
  assert.match((w.parseManifest("{") as { error: string }).error, /not valid JSON/);
  assert.match((w.parseManifest("[]") as { error: string }).error, /non-empty/);
  assert.match((w.parseManifest(JSON.stringify([file, { ...file, bytes: 1.5 }])) as { error: string }).error, /^File 2 /);
});

test("OP-W04 a refusal is safe copy: an unanswered mutation is not confirmed, a stale key or form is a conflict, field errors are listed", () => {
  const net = { kind: "unavailable", status: null, reason: "network" } as const;
  assert.match(w.problem(net, true).message, /Outcome not confirmed/);
  assert.doesNotMatch(w.problem(net, false).message, /Outcome not confirmed/);
  const err = (status: number, fieldErrors: { field: string; code: string; message: string }[] = []) =>
    ({ kind: "error", status, code: "x", message: "raw server text", requestId: "r", retryable: false, fieldErrors, operationId: null, resourceId: null }) as const;
  assert.match(w.problem(err(409), true).message, /already submitted with different values/);
  assert.match(w.problem(err(403), true).message, /does not allow/);
  assert.match(w.problem(err(503), true).message, /Outcome not confirmed/);
  const invalid = w.problem(err(422, [{ field: "source.commit", code: "value_error", message: "mutable_ref: an import is pinned to a 40-hex commit" }]), true);
  assert.deepEqual(invalid.fields, ["source.commit: mutable_ref: an import is pinned to a 40-hex commit"]);
  for (const s of [401, 403, 404, 409, 410, 413, 422, 503]) assert.doesNotMatch(w.problem(err(s), true).message, /raw server text/);
});

test("OP-W05 every call is the actor's workspace with the session's own token, no-store, and the form's Idempotency-Key", async () => {
  const seen: { url: string; init: RequestInit }[] = [];
  const port = api.artifactClient({
    baseUrl: "http://api.test/", session: () => ({ token: "session-jwt" }),
    fetch: (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify({ operation_id: "op-9", kind: "artifact.import", state: "queued", created_at: "t", updated_at: "t" }), { status: 202 });
    }) as unknown as typeof fetch,
  });
  const actor = { providerId: "11111111-1111-4111-8111-111111111111" };
  const answer = await port.call(actor, "post", "/lab/v1/artifacts/imports", {
    body: { project_id: "p1", source: { host: "huggingface.co", repo: "org/model", commit: "a".repeat(40) }, files: [] },
    idempotencyKey: "form-key-1", query: { provider_org_id: "99999999-9999-4999-8999-999999999999" },
  });
  assert.ok(answer.ok && answer.data.operation_id === "op-9");
  const [{ url, init }] = seen;
  assert.equal(url, `http://api.test/lab/v1/artifacts/imports?provider_org_id=${actor.providerId}`, "the actor's workspace wins over a query value");
  const headers = init.headers as Record<string, string>;
  assert.deepEqual([headers.authorization, headers["idempotency-key"], init.cache, init.method], ["Bearer session-jwt", "form-key-1", "no-store", "POST"]);
  assert.ok(api.artifactPort({}) === null, "unconfigured: no port (the wizard says unavailable)");
  assert.ok(api.artifactPort({ LAB_API_URL: "http://api.test" }) === null, "no Lab config: no port");
});

test("OP-W06 the port's paths, methods and the fields the wizard reads exist in the checked OpenAPI artifact", () => {
  // The real checkout's artifact, also from a mutant runner's copy (its node_modules links the real Lab).
  const doc = JSON.parse(readFileSync(join(realpathSync(join(LAB, "node_modules")), "../../infrx-api/openapi/consumer.json"), "utf8"));
  const source = readFileSync(join(LAB, "app/(provider)/models/new/api.ts"), "utf8");
  const declared = [...source.matchAll(/^  "(\/lab\/v1\/[^"]+)": \{([^\n]*(?:\n    [^\n]*)*)/gm)].map((m) => [m[1], [...m[2].matchAll(/\b(get|post):/g)].map((x) => x[1])] as const);
  assert.equal(declared.length, 8);
  for (const [path, methods] of declared) for (const method of methods) assert.ok(doc.paths[path]?.[method], `${method} ${path} is not in consumer.json`);
  const props = (name: string) => Object.keys(doc.components.schemas[name].properties);
  const READS: Record<string, string[]> = {
    Project: ["project_id", "slug", "name", "description", "created_at"],
    OperationDoc: ["operation_id", "kind", "state", "phase", "resource_id", "retry_after_s", "error", "updated_at"],
    Upload: ["upload_id", "manifest_sha256", "expires_at"],
    PartGrant: ["relative_path", "url", "expires_s"],
    Artifact: ["artifact_id", "source", "source_repo", "source_commit", "files", "manifest_sha256", "compatibility", "verified_at"],
    RevisionDoc: ["serving_version_id", "public_model_id", "revision_label", "model_revision", "artifact_id"],
    Compatibility: ["supported", "profile", "reasons"],
    ImportRequest: ["project_id", "source", "secret_ref", "files"],
    ProjectRequest: ["name", "slug", "description"],
    RevisionRequest: ["artifact_id"],
  };
  for (const [schema, fields] of Object.entries(READS)) for (const f of fields) assert.ok(props(schema).includes(f), `${schema}.${f}`);
  assert.deepEqual(doc.components.schemas.OperationDoc.properties.state.enum.sort(), ["cancel_requested", "cancelled", "failed", "queued", "running", "succeeded"]);
});
