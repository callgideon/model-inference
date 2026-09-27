// N4 CONSOLE-FLOWS / DATA-IMPORT / DATA-RIGHTS: the server actions' decisions. The provider is the
// guarded workspace's, a viewer never reaches the backend, malformed input never leaves the Lab, and
// a backend failure is never success.
import assert from "node:assert/strict";
import test from "node:test";
import type { Membership } from "../../lib/auth/access.ts";
import { deriveVersion, exportVersion, MAX_UPLOAD_BYTES, PREVIEW_BYTES, previewImport, startImport } from "../../lib/services/datasets/flows.ts";
import { fail, recordingPort } from "./fake.ts";

const DEV: Membership = { providerId: "11111111-1111-4111-8111-111111111111", providerName: "Acme", role: "developer" };
const VIEWER: Membership = { ...DEV, role: "viewer" };
const SPEC = JSON.stringify({ format: "infrx.dataset_import.1" });
const UUID = "22222222-2222-4222-8222-222222222222";

function form(fields: Record<string, string | Blob>): FormData {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.append(k, v);
  return f;
}
const file = (text: string) => new Blob([text], { type: "application/x-ndjson" });
const derive = (extra: Record<string, string> = {}) => form({ dataset_id: UUID, version: "1", seed: "7", train_bp: "8000", validation_bp: "1000", base: "lab:dataset:b", ...extra });

test("N4-F01 a viewer is refused before any backend call", async () => {
  const { port, calls } = recordingPort();
  for (const state of [
    await previewImport(port, VIEWER, form({ spec: SPEC, file: file("{}\n") })),
    await startImport(port, VIEWER, form({ spec: SPEC, file: file("{}\n") })),
    await deriveVersion(port, VIEWER, derive()),
    await exportVersion(port, VIEWER, form({ dataset_ref: "d", ttl_s: "60" }), UUID),
  ]) assert.equal(state.status, "error");
  assert.deepEqual(calls, []);
});

test("N4-F02 the provider is the guarded workspace's, never a submitted field", async () => {
  const { port, calls } = recordingPort();
  await startImport(port, DEV, form({ spec: SPEC, file: file('{"q":1}\n'), providerId: "33333333-3333-4333-8333-333333333333", accept_rejects: "on" }));
  await exportVersion(port, DEV, form({ dataset_ref: "lab:dataset:d", ttl_s: "60", provider_org_id: "x" }), UUID);
  assert.deepEqual(calls.map((c) => c.args[0]), [DEV.providerId, DEV.providerId]);
  assert.deepEqual(calls[0].args.slice(1), [{ format: "infrx.dataset_import.1" }, '{"q":1}\n', true]);
  assert.deepEqual(calls[1].args.slice(1), ["lab:dataset:d", UUID, 60]);
});

test("N4-F03 a malformed mapping or a missing file never leaves the Lab; a preview reads only the head", async () => {
  const { port, calls } = recordingPort();
  for (const spec of ["{not json", "[1]", "null", ""]) {
    assert.deepEqual(await previewImport(port, DEV, form({ spec, file: file("{}\n") })), { status: "error", message: "The import spec is not a JSON object." });
  }
  assert.equal((await startImport(port, DEV, form({ spec: SPEC }))).status, "error");
  assert.equal((await previewImport(port, DEV, form({ spec: SPEC, file: file("") }))).status, "error");
  assert.deepEqual(calls, []);
  await previewImport(port, DEV, form({ spec: SPEC, file: file("x".repeat(PREVIEW_BYTES + 10)) }));
  assert.equal((calls[0].args[2] as string).length, PREVIEW_BYTES);
});

test("N4-F04 an upload over the bound is refused before the backend", async () => {
  const { port, calls } = recordingPort();
  const state = await startImport(port, DEV, form({ spec: SPEC, file: file("x".repeat(MAX_UPLOAD_BYTES + 1)) }));
  assert.equal(state.status, "error");
  assert.deepEqual(calls, []);
  await startImport(port, DEV, form({ spec: SPEC, file: file("x".repeat(MAX_UPLOAD_BYTES)) }));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args[3], false);
});

test("N4-F05 a derivation needs a UUID, whole numbers within 10000 basis points and a parent", async () => {
  const { port, calls } = recordingPort();
  for (const bad of [{ dataset_id: "D" }, { version: "0" }, { seed: "1.5" }, { train_bp: "9500", validation_bp: "600" }, { base: "", add: " " }]) {
    assert.equal((await deriveVersion(port, DEV, derive(bad))).status, "error", JSON.stringify(bad));
  }
  assert.deepEqual(calls, []);
  await deriveVersion(port, DEV, derive({ base: "", add: "lab:dataset:a\nlab:dataset:c" }));
  assert.deepEqual(calls[0].args, [DEV.providerId, { datasetId: UUID, version: 1, seed: 7, trainBp: 8000, validationBp: 1000, base: null, add: ["lab:dataset:a", "lab:dataset:c"] }]);
});

test("N4-F06 an export lives 1 s to 7 days and names a version", async () => {
  const { port, calls } = recordingPort();
  for (const bad of [{ ttl_s: "0" }, { ttl_s: String(7 * 86_400 + 1) }, { ttl_s: "x" }, { dataset_ref: "" }]) {
    assert.equal((await exportVersion(port, DEV, form({ dataset_ref: "d", ttl_s: "60", ...bad }), UUID)).status, "error");
  }
  assert.deepEqual(calls, []);
  await exportVersion(port, DEV, form({ dataset_ref: "d", ttl_s: String(7 * 86_400) }), UUID);
  assert.equal(calls[0].args[3], 7 * 86_400);
});

test("N4-F07 a backend failure is an error state carrying its leaks and report, never success", async () => {
  const leaks = [{ samples: ["a"], splits: ["train", "holdout"] }];
  const report = { accepted: 0, rejected: [{ line: 1, reason: "not_json", detail: "" }], datasetRef: null, sourceRef: null };
  const { port } = recordingPort((method) => (method === "derive" ? fail("conflict", { leaks }) : fail("invalid", { report })));
  const derived = await deriveVersion(port, DEV, derive());
  assert.equal(derived.status === "error" && derived.leaks, leaks);
  const started = await startImport(port, DEV, form({ spec: SPEC, file: file("{}\n") }));
  assert.equal(started.status === "error" && started.report, report);
  assert.match(started.status === "error" ? started.message : "", /refused as invalid/);
});
