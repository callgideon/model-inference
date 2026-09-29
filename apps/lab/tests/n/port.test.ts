// N4 CONSOLE-FLOWS / DATA-RIGHTS at the Lab's edge: the backend adapter sends only the user's own
// session token to the provider-scoped path, and anything it does not understand fails closed.
import assert from "node:assert/strict";
import test from "node:test";
import { failureStatus, httpDatasets, offlineDatasets, type DatasetsPort } from "../../lib/services/datasets/port.ts";

const P = "11111111-1111-4111-8111-111111111111";
type Sent = { url: string; init: RequestInit };

function backend(status: number, body: unknown, raw = false) {
  const sent: Sent[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return new Response(raw ? String(body) : JSON.stringify(body), { status });
  }) as unknown as typeof globalThis.fetch;
  return { port: httpDatasets({ baseUrl: "http://api.test/", token: "user-session-token", fetch }), sent };
}

const JOB = { import_id: "i-1", state: "published", error: null, report: { accepted: 2, rejected: [], dataset_ref: "lab:dataset:x", source_ref: "lab:source:y" } };

test("N4-P01 every call is the provider-scoped path with the user's own token and nothing else", async () => {
  const { port, sent } = backend(200, JOB);
  await port.importJob(P, "i-1");
  await port.importJob("a/../b", "../x");
  assert.equal(sent[0].url, `http://api.test/lab/v1/providers/${P}/datasets/imports/i-1`);
  assert.equal(sent[1].url, "http://api.test/lab/v1/providers/a%2F..%2Fb/datasets/imports/..%2Fx");
  assert.deepEqual(sent[0].init.headers, { authorization: "Bearer user-session-token" });
  assert.equal(sent[0].init.cache, "no-store");
  const post = backend(200, JOB);
  await post.port.startImport(P, { format: "infrx.dataset_import.1" }, "{}\n", true);
  assert.equal(post.sent[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(post.sent[0].init.body)), { spec: { format: "infrx.dataset_import.1" }, body: "{}\n", accept_rejects: true });
  const again = await post.port.requeue(P, "../i-1");
  assert.deepEqual([post.sent[1].url, post.sent[1].init.method, again.ok && again.value.importId],
    [`http://api.test/lab/v1/providers/${P}/datasets/imports/..%2Fi-1/requeue`, "POST", "i-1"]);
});

test("N4-P02 refusals are named failures, carrying leaks and the rejection report", async () => {
  for (const [status, error] of [[400, "invalid"], [403, "denied"], [404, "not_found"], [409, "conflict"], [410, "gone"], [500, "unavailable"], [503, "unavailable"]] as const) {
    const got = await backend(status, { detail: "why" }).port.versions(P);
    assert.deepEqual(got, { ok: false, error, detail: "why" }, `HTTP ${status}`);
  }
  const leaks = [{ samples: ["s1", "s2"], splits: ["holdout", "train"] }];
  assert.deepEqual(await backend(409, { detail: "leak", leaks }).port.derive(P, { datasetId: "d", version: 1, seed: 0, trainBp: 1, validationBp: 1, base: "b", add: [] }),
    { ok: false, error: "conflict", detail: "leak", leaks });
  const report = { accepted: 1, rejected: [{ line: 2, reason: "not_json", detail: "" }], dataset_ref: null, source_ref: null };
  const refused = await backend(400, { detail: "rows", report }).port.startImport(P, {}, "", false);
  assert.equal(refused.ok === false && refused.report?.rejected[0].reason, "not_json");
  assert.equal((await backend(409, { detail: "x", leaks: "no" }).port.versions(P)).ok === false, true);
  assert.deepEqual(await backend(409, { detail: "x", leaks: [{ samples: 1 }] }).port.versions(P),
    { ok: false, error: "unavailable", detail: "the backend's refusal was malformed" });
});

test("N4-P03 a success the Lab does not understand is unavailable, never success", async () => {
  const bad = [
    { ...JOB, report: { ...JOB.report, dataset_ref: null } },          // published without a dataset
    { ...JOB, state: "done" },                                            // unknown state
    { ...JOB, report: { ...JOB.report, accepted: -1 } },
  ];
  for (const body of bad) assert.equal((await backend(200, body).port.importJob(P, "i")).ok, false, JSON.stringify(body));
  const sample = { sample_id: "s", split: "test", source_ref: "r", grant_ref: "g", trace: null, restricted: null };
  const status = await backend(200, { dataset_ref: "d", parent_refs: [], samples: [sample] }).port.version(P, "d");
  assert.deepEqual(status, { ok: false, error: "unavailable", detail: "the datasets service answered with something unreadable" });
  assert.equal((await backend(200, "not json", true).port.versions(P)).ok, false);
  const good = await backend(200, JOB).port.importJob(P, "i");
  assert.deepEqual(good, { ok: true, value: { importId: "i-1", state: "published", error: null, report: { accepted: 2, rejected: [], datasetRef: "lab:dataset:x", sourceRef: "lab:source:y" } } });
});

test("N4-P04 an unreachable or unconfigured service is unavailable for every call", async () => {
  const down = httpDatasets({ baseUrl: "http://api.test", token: "t", fetch: (async () => { throw new TypeError("refused"); }) as unknown as typeof fetch });
  const got = await down.versions(P).catch((error: unknown) => ({ crashed: String(error) }));    // a crash is the defect
  assert.deepEqual(got, { ok: false, error: "unavailable", detail: "the datasets service could not be reached" });
  const offline = offlineDatasets();
  for (const method of ["preview", "startImport", "importJob", "requeue", "versions", "version", "derive", "exportVersion", "readPart"] as (keyof DatasetsPort)[]) {
    const got = await (offline[method] as (...a: unknown[]) => Promise<{ ok: boolean; error?: string }>)(P, "x", "y", false);
    assert.equal(got.ok === false && got.error, "unavailable", method);
  }
  const part = await backend(200, '{"sample_id":"s"}\n', true).port.readPart(P, "e", 0);
  assert.deepEqual(part, { ok: true, value: '{"sample_id":"s"}\n' });
});

test("N4-P05 a download route answers a failure with its status, never 200", () => {
  assert.deepEqual(["denied", "not_found", "invalid", "conflict", "gone", "unavailable"].map((error) => failureStatus({ ok: false, error, detail: "" } as never)),
    [403, 404, 400, 409, 410, 503]);
});
