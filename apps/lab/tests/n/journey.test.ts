// N4 journey on the REAL N1/N2/N3 (DATA-IMPORT, DATA-SPLIT, CONSOLE-FLOWS, revoked grants): the server actions' cores
// and the Lab's HTTP port against `backend.py` - the production datasets route (WR-N4-1) over 0051's durable
// import queue, the datasets pool's own pass, real D7 and L2 on the task-local PostgreSQL. The import resume is
// WR-AP10C-3's oracle (tests/ap10/test_import_resume_pg.py is its Python half): an interrupted import is not
// failed - its lease lapses and the same id resumes; a failed one stays failed (R243), a re-POST answers it,
// and "Import again" (`requeueImport`) is a new id. Skipped unless LAB_N_REAL=1 (an explicit task-local key
// and Docker):
//   LAB_N_REAL=1 INFRX_D_TASK=l4 node --test tests/n/journey.test.ts
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import type { Membership } from "../../lib/auth/access.ts";
import { deriveVersion, exportVersion, previewImport, requeueImport, startImport } from "../../lib/services/datasets/flows.ts";
import { httpDatasets, type DatasetsPort, type ImportJob } from "../../lib/services/datasets/port.ts";
import { importView, leakageWarnings, RESTRICTED_COPY, restrictedCopy, splitSummary } from "../../lib/services/datasets/views.ts";

const REAL = process.env.LAB_N_REAL === "1";
const lab = resolve(import.meta.dirname, "../..");
const FIXTURES = resolve(lab, "../infrx-api/tests/n/imports/fixtures");
type World = { provider: string; other: string; dev: string; viewer: string; other_dev: string; consumer: string; grant_ref: string };

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/n/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
  const line = await new Promise<string>((done, failed) => {
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
      const ready = out.split("\n").find((l) => l.startsWith("READY "));
      if (ready) done(ready);
    });
    child.on("exit", (code) => failed(new Error(`backend exited ${code}: ${out}`)));
  });
  const [, port, world] = line.match(/^READY (\d+) (.*)$/)!;
  return { url: `http://127.0.0.1:${port}`, world: JSON.parse(world), stop: () => child.kill("SIGINT") };
}

function form(fields: Record<string, string | Blob>): FormData {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.append(k, v);
  return f;
}

async function read(port: DatasetsPort, provider: string, id: string): Promise<ImportJob> {
  const job = await port.importJob(provider, id);
  assert.equal(job.ok, true, JSON.stringify(job));
  return job.ok ? job.value : null!;
}

/** A test-only door on the backend: the datasets pool's pass, the lease's clock, the grantor. */
async function door(url: string, path: string, body: object = {}): Promise<Record<string, unknown>> {
  const r = await fetch(`${url}/_test/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  assert.equal(r.status, 200, `${path}: ${await r.clone().text()}`);
  return r.json();
}
const passed = (succeeded: number, failed: number, retry: number) => ({ succeeded, failed, retry });

test("N4-J01 import, interrupted and resumed, then a frozen version, its splits, an export and a revocation, on real N1/N2", { skip: !REAL && "LAB_N_REAL=1 with a task-local key" }, async () => {
  const { url, world, stop } = await backend();
  try {
    const as = (user: string) => httpDatasets({ baseUrl: url, token: user });
    const port = as(world.dev);
    const dev: Membership = { providerId: world.provider, providerName: "NemoStation", role: "developer", capabilities: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"] };
    const spec = { ...JSON.parse(readFileSync(resolve(FIXTURES, "benchmark.spec.json"), "utf8")), grant_ref: world.grant_ref };
    const rows = readFileSync(resolve(FIXTURES, "benchmark.jsonl"), "utf8");
    const file = (text: string) => new Blob([text]);

    // a malformed mapping: the preview names each row's refusal; a spec with an unknown key is refused
    const bad = await previewImport(port, dev, form({ spec: JSON.stringify({ ...spec, fields: { content: "nope" } }), file: file(rows) }));
    assert.equal(bad.status, "ok");
    assert.deepEqual(bad.status === "ok" && [...new Set(bad.value.rows.map((r) => r.reason))], ["missing_field"]);
    const strict = await previewImport(port, dev, form({ spec: JSON.stringify({ ...spec, extra: 1 }), file: file(rows) }));
    assert.match(strict.status === "error" ? strict.message : "", /refused as invalid/);
    const good = await previewImport(port, dev, form({ spec: JSON.stringify(spec), file: file(rows) }));
    assert.deepEqual(good.status === "ok" && good.value.rows.map((r) => r.line), [1, 2, 3, 4, 6]);

    // an interrupted import (the datasets worker dies mid-staging) is not failed: once its lease lapses the
    // next pass resumes the same id from the staged chunks to published, once
    const started = await startImport(port, dev, form({ spec: JSON.stringify(spec), file: file(rows) }));
    assert.deepEqual(started.status === "ok" && [started.value.importId, started.value.state], [spec.import_id, "running"]);
    assert.deepEqual(await door(url, "pass", { crash_after_puts: 2 }), passed(0, 0, 1));
    const interrupted = await read(port, world.provider, spec.import_id);
    assert.deepEqual([interrupted.state, importView(interrupted).again], ["running", false], "an interrupted import is not failed");
    assert.deepEqual(await door(url, "pass"), passed(0, 0, 0), "nothing is claimed while the lease holds");
    await door(url, "lapse");
    assert.deepEqual(await door(url, "pass"), passed(1, 0, 0));
    const job = await read(port, world.provider, spec.import_id);
    assert.deepEqual([job.state, job.report?.accepted, job.report?.rejected], ["published", 5, []]);
    const imported = job.report!.datasetRef!;
    const replay = await startImport(port, dev, form({ spec: JSON.stringify(spec), file: file(rows) }));
    assert.deepEqual(replay.status === "ok" && [replay.value.state, replay.value.report?.datasetRef], ["published", imported], "the same id is the same job");

    // a second import holding a near-duplicate of a holdout question; its upload is lost before the pool reads
    // it, so it fails for good (R243): a re-POST of the failed id answers it and resumes nothing, "Import again"
    // is a new id that publishes, and the failed id stays failed
    const spec2 = { ...spec, import_id: "1a000000-0000-4000-8000-000000000002", dataset_id: "da000000-0000-4000-8000-000000000002", fields: { content: "question", group: "episode" } };
    const rows2 = '{"question": "what is shown in the FIRST frame?", "episode": "x1"}\n{"question": "Where is the exit?", "episode": "x2"}\n';
    await startImport(port, dev, form({ spec: JSON.stringify(spec2), file: file(rows2) }));
    assert.deepEqual(await door(url, "pass", { lose: spec2.import_id }), passed(0, 1, 0));
    const lost = await read(port, world.provider, spec2.import_id);
    assert.deepEqual([lost.state, importView(lost).tone, importView(lost).again], ["failed", "error", true]);
    const again = await startImport(port, dev, form({ spec: JSON.stringify(spec2), file: file(rows2) }));
    assert.deepEqual(again.status === "ok" && [again.value.importId, again.value.state], [spec2.import_id, "failed"], "a re-POST does not resume a failed id");
    assert.deepEqual(await door(url, "pass"), passed(0, 0, 0), "and queues nothing");
    const requeued = await requeueImport(port, dev, form({ import_id: spec2.import_id }));
    const successor = requeued.status === "ok" ? requeued.value.importId : "";
    assert.ok(successor !== "" && successor !== spec2.import_id, JSON.stringify(requeued));
    assert.deepEqual(await door(url, "pass"), passed(1, 0, 0));
    const second = (await read(port, world.provider, successor)).report!.datasetRef!;
    assert.equal((await read(port, world.provider, spec2.import_id)).state, "failed", "the failed id stays failed");

    // freeze a version over the import: the holdout stays, the relative is left out and warned about
    const derived = await deriveVersion(port, dev, form({
      dataset_id: "da000000-0000-4000-8000-0000000000f4", version: "1", seed: "7", train_bp: "8000", validation_bp: "2000", base: imported, add: second,
    }));
    assert.equal(derived.status, "ok", JSON.stringify(derived));
    const frozen = derived.status === "ok" ? derived.value : null!;
    assert.deepEqual(leakageWarnings({ derived: frozen }).filter((w) => w.startsWith("Omitted")).length, 1);
    const listed = await port.versions(world.provider);
    assert.deepEqual(listed.ok && listed.value.map((v) => v.datasetRef).sort(), [imported, second, frozen.datasetRef].sort());
    const status = await port.version(world.provider, frozen.datasetRef);
    assert.equal(status.ok, true);
    const summaryOf = (s: typeof status) => (s.ok ? splitSummary(s.value) : []).map((r) => [r.split, r.samples, r.restricted]);
    assert.deepEqual(summaryOf(status), [["train", 2, 0], ["validation", 2, 0], ["holdout", 2, 0]]);

    // export: never the holdout; the part is read through the backend's gate
    const exported = await exportVersion(port, dev, form({ dataset_ref: frozen.datasetRef, ttl_s: "600" }), "e4000000-0000-4000-8000-000000000001");
    assert.equal(exported.status, "ok", JSON.stringify(exported));
    const record = exported.status === "ok" ? exported.value : null!;
    assert.deepEqual(record.omitted.map((o) => o.reason), ["holdout", "holdout"]);
    const part = await port.readPart(world.provider, record.exportId, 0);
    const items = part.ok ? part.value.trim().split("\n").map((l) => JSON.parse(l)) : [];
    assert.equal(items.length, 4);
    assert.equal(items.some((i) => i.split === "holdout"), false);

    // cross-provider deep links, a viewer and a consumer-only user
    const foreign = as(world.other_dev);
    assert.equal((await foreign.version(world.provider, frozen.datasetRef)).ok === false && "x", "x");
    assert.deepEqual(await foreign.version(world.other, frozen.datasetRef).then((r) => !r.ok && r.error), "not_found");
    assert.deepEqual(await foreign.versions(world.provider).then((r) => !r.ok && r.error), "not_found");
    assert.deepEqual(await as(world.viewer).versions(world.provider).then((r) => !r.ok && r.error), "denied");
    assert.deepEqual(await as(world.consumer).version(world.provider, frozen.datasetRef).then((r) => !r.ok && r.error), "not_found");
    assert.deepEqual(await as(world.other_dev).readPart(world.provider, record.exportId, 0).then((r) => !r.ok && r.error), "not_found");

    // revocation (1-DSL4-1): the grantor revokes through L2's real RPC; the export made before it
    // stops serving the revoked items at once, and the version browser explains every sample
    await door(url, "revoke");
    const after = await port.readPart(world.provider, record.exportId, 0);
    assert.deepEqual(after.ok && after.value.trim(), "");
    const revoked = await port.version(world.provider, frozen.datasetRef);
    assert.equal(revoked.ok, true);
    const restricted = revoked.ok ? revoked.value.samples.map((s) => s.restricted) : [];
    assert.deepEqual([...new Set(restricted)], ["grant_not_current"]);
    assert.equal(restricted.length, 6);
    assert.deepEqual(summaryOf(revoked), [["train", 2, 2], ["validation", 2, 2], ["holdout", 2, 2]]);
    assert.equal(restrictedCopy(restricted[0]!), RESTRICTED_COPY.grant_not_current);
  } finally {
    stop();
  }
});
