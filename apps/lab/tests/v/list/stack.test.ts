// V1M real L2 evidence (CONSOLE-TENANT, LAB-ACCESS, TRACE-TENANT): the Lab's trace adapter and list view
// against lab-api's provider trace read as merged, over the real L2 grants on the task-local PostgreSQL
// and T2I/T3 on the pinned ClickHouse (`backend.py`). Skipped unless LAB_V1M_REAL=1 (needs Docker, the
// lab-v1m key and infrx-t2i-clickhouse on 57540):
//   LAB_V1M_REAL=1 INFRX_D_TASK=lab-v1m node --test tests/v/list/stack.test.ts
// Outside the mutant suite (like V2's feedback-postgrest): its oracle is the real route, not Lab code.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import type { Actor, TraceDetail } from "../../../components/traces/detail/port.ts";
import { buildListView, LIST_COPY } from "../../../components/traces/list/view-model.ts";
import { httpTraces } from "../../../lib/services/traces/port.ts";

const REAL = process.env.LAB_V1M_REAL === "1";
const lab = resolve(import.meta.dirname, "../../..");
type Names = "granted" | "ungranted" | "lost" | "minimal" | "foreign" | "deleted" | "expired";
type World = { A: string; B: string; C1: string; C2: string; ids: Record<Names, string>; tokens: Record<"dev_a" | "dev_b" | "viewer_a" | "consumer_only", string> };

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/v/list/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
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

test("V1M-S01..S06 the six L2 cases on the real route: own requests, another provider, consumer-only, revoked, expired, capture off and lost", { skip: !REAL && "LAB_V1M_REAL=1 (Docker, lab-v1m, infrx-t2i-clickhouse)" }, async (t) => {
  const { url, world: w, stop } = await backend();
  t.after(stop);
  const as = (who: keyof World["tokens"]) => httpTraces({ baseUrl: url, token: async () => w.tokens[who] });
  const A: Actor = { providerId: w.A, role: "developer" };
  const B: Actor = { providerId: w.B, role: "developer" };
  const byName = (items: TraceDetail[]) => Object.fromEntries(items.map((d) => [Object.entries(w.ids).find(([, id]) => id === d.request_id)![0], d]));
  const labels = async () => {
    const page = await as("dev_a").list(A, null);
    assert.ok(page.ok, JSON.stringify(page));
    const view = buildListView(page, null);
    assert.ok(view.kind === "rows");
    return Object.fromEntries(view.rows.map((r) => [Object.entries(w.ids).find(([, id]) => id === r.requestId)![0], r.content]));
  };

  await t.test("S01 provider A's developer lists its own deployment's requests and reads one; the row links to its page", async () => {
    const page = await as("dev_a").list(A, null);
    assert.ok(page.ok);
    const items = byName(page.value.items);
    assert.deepEqual(Object.keys(items).sort(), ["expired", "granted", "lost", "minimal", "ungranted"], "never another provider's, a deleted one, or one never captured");
    assert.equal(items.granted.access, "content");
    const view = buildListView(page, null);
    assert.ok(view.kind === "rows" && view.rows.every((r) => r.href === `/requests/${r.requestId}`));
    const one = await as("dev_a").detail(A, w.ids.ungranted);
    assert.ok(one.ok && one.value.request_id === w.ids.ungranted);
  });

  await t.test("S02 provider B learns nothing: A's request is not_found as B and as A, and B's own list is empty", async () => {
    assert.deepEqual(await as("dev_b").detail(B, w.ids.ungranted), { ok: false, reason: "not_found" });
    assert.deepEqual(await as("dev_b").detail(A, w.ids.ungranted), { ok: false, reason: "not_found" });
    assert.deepEqual(await as("dev_b").list(A, null), { ok: false, reason: "not_found" });
    assert.deepEqual(buildListView(await as("dev_b").list(B, null), null), { kind: "empty", message: LIST_COPY.empty });
    assert.deepEqual(await as("dev_a").detail(A, w.ids.foreign), { ok: false, reason: "not_found" });
    assert.deepEqual(await as("dev_a").detail(A, w.ids.deleted), { ok: false, reason: "not_found" });
  });

  await t.test("S03 a consumer-only user and a viewer are denied the list and the request", async () => {
    for (const who of ["consumer_only", "viewer_a"] as const) {
      assert.deepEqual(await as(who).list(A, null), { ok: false, reason: "denied" }, who);
      assert.deepEqual(await as(who).detail(A, w.ids.ungranted), { ok: false, reason: "denied" }, who);
    }
    const raw = await fetch(`${url}/lab/v1/traces?provider_org_id=${w.A}`);
    assert.equal(raw.status, 401, "no session");
  });

  await t.test("S04..S06 metadata rows carry no organization, size or content; expired content, capture off and lost capture read honestly", async () => {
    const raw = await (await fetch(`${url}/lab/v1/traces?provider_org_id=${w.A}`, { headers: { authorization: `Bearer ${w.tokens.dev_a}` } })).json();
    const ungranted = raw.data.find((d: { request_id: string }) => d.request_id === w.ids.ungranted);
    assert.equal(ungranted.access, "metadata");
    assert.doesNotMatch(JSON.stringify(ungranted), new RegExp(`${w.C2}|content_bytes|grantor|key_id|prompt`));
    assert.deepEqual(await labels(), { minimal: "Not captured", lost: "Lost: the capture queue was full", ungranted: "Metadata only", granted: "Shared", expired: "Expired" });
  });

  await t.test("S04 after C1 revokes its grant the next read is metadata only", async () => {
    assert.equal((await fetch(`${url}/_test/revoke`, { method: "POST" })).status, 200);
    const labelled = await labels();
    assert.equal(labelled.granted, "Metadata only");
    const one = await as("dev_a").detail(A, w.ids.granted);
    assert.ok(one.ok && one.value.access === "metadata" && !("grantor_org_id" in one.value));
  });
});
