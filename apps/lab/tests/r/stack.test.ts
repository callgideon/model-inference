// R4 swap, real evidence: the journeys J01-J04 rerun through the Lab's releases adapter against
// lab-api's `/lab/v1/releases` + `/lab/v1/optimizations` as merged, over the real D9 with R2's own
// controller deciding and the real L2 on the task-local PostgreSQL (`backend.py`, key r2). Skipped
// unless LAB_R4_REAL=1 (needs Docker and the r2 key):
//   LAB_R4_REAL=1 INFRX_D_TASK=r2 node --test tests/r/stack.test.ts
// Outside the mutant suite (like V1M's stack): its oracle is the real route, not Lab code.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import { httpReleases } from "../../lib/services/rollouts/http.ts";
import type { Actor, Records } from "../../lib/services/rollouts/port.ts";
import { releaseRows } from "../../lib/services/rollouts/view.ts";

const REAL = process.env.LAB_R4_REAL === "1";
const lab = resolve(import.meta.dirname, "../..");
type Who = "admin" | "dev" | "viewer" | "other_dev" | "consumer";
type World = { A: string; B: string; operator: string; controller: string; servings_run_ids: string[]; tokens: Record<Who, string> };

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/r/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
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

const value = <T>(r: { ok: true; value: T } | { ok: false; reason: string }): T => {
  assert.ok(r.ok, `refused: ${JSON.stringify(r)}`);
  return r.value;
};
const reason = (r: { ok: boolean; reason?: string }) => (r.ok ? "ok" : r.reason);

test("R4-S01..S04 the release journeys on the real route: guardrail rollback, promotion, unsafe proposals, a stale approval", { skip: !REAL && "LAB_R4_REAL=1 (Docker, r2)" }, async (t) => {
  const { url, world: w, stop } = await backend();
  t.after(stop);
  const as = (who: Who) => httpReleases({ baseUrl: url, token: async () => w.tokens[who] });
  const hook = async (path: string, body: unknown) => {
    const r = await fetch(`${url}/_test/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    assert.equal(r.status, 200, path);
    return r.json();
  };
  const admin: Actor = { providerId: w.A, role: "administrator" };
  const launch = async (tag: number): Promise<string> => (await hook("launch", { tag })).policy_ref;
  const mine = async (actor: Actor = admin): Promise<Records> => value(await as("admin").releases(actor));
  const one = (rec: Records, ref: string) => rec.releases.find((r) => r.policyRef === ref)!;

  await t.test("S01 (J01) guardrail rollback: R2's rollback verdict is one D9 transition and the page shows it from the records", async () => {
    const ref = await launch(1);
    assert.equal((await hook("step", { policy_ref: ref, errors: 21 })).action, "rollback");
    assert.equal((await hook("step", { policy_ref: ref, errors: 21 })).action, "rolled_back", "the next pass only converges");
    const rec = await mine();
    assert.deepEqual([one(rec, ref).state, one(rec, ref).fence], ["rolled_back", 2], "a breach rolls back exactly once");
    const decided = rec.decisions.filter((d) => d.policyRef === ref);
    assert.deepEqual(decided.map((d) => [d.decision, d.reasons, d.decidedBy]), [["rollback", ["error_rate"], w.controller]]);
    const row = releaseRows("administrator", rec).find((r) => r.id === ref)!;
    assert.equal(row.status, `rolled back · serving returns to ${one(rec, ref).baselineRef}`);
    assert.equal(row.lineage.length, 1);
    assert.match(row.lineage[0], /^rollback by .* error_rate$/);
    assert.deepEqual(row.actions, []);
    await hook("step", { policy_ref: ref, report: "accept" });
    assert.equal(one(await mine(), ref).state, "rolled_back", "a rolled-back release stays rolled back");
  });

  await t.test("S02 (J02) promotion: expand verdict → administrator proposal → operator approval → approved with its evidence", async () => {
    const ref = await launch(2);
    assert.equal((await hook("step", { policy_ref: ref, report: "accept" })).action, "expand");
    const before = await mine();
    assert.deepEqual(releaseRows("administrator", before).find((r) => r.id === ref)!.actions, ["expand", "rollback"]);
    const p = value(await as("admin").propose(admin, "expand", ref, one(before, ref).fence));
    assert.equal(one(await mine(), ref).state, "running", "a proposal changes nothing");
    assert.equal(releaseRows("administrator", await mine()).find((r) => r.id === ref)!.pending, "expand proposed · awaiting operator approval");
    assert.equal((await hook("decide", { proposal_id: p.proposalId, approve: true })).result, "ok");
    const rec = await mine();
    assert.deepEqual([one(rec, ref).state, rec.proposals.find((x) => x.proposalId === p.proposalId)!.state], ["approved", "approved"]);
    const expanded = rec.decisions.filter((d) => d.policyRef === ref);
    assert.deepEqual(expanded.map((d) => [d.decision, d.decidedBy]), [["expand", w.operator]]);
    assert.deepEqual(expanded[0].evidenceRefs.map((r) => r.split(":")[3].split("@")[0]).sort(), [...w.servings_run_ids].sort(), "the approval carries its two B1 runs as evidence");
    const back = value(await as("admin").propose(admin, "rollback", ref, one(rec, ref).fence));
    assert.equal((await hook("decide", { proposal_id: back.proposalId, approve: true })).result, "ok");
    const after = await mine();
    assert.deepEqual([one(after, ref).state, after.decisions.filter((d) => d.policyRef === ref).at(-1)!.decision], ["rolled_back", "rollback"]);
  });

  await t.test("S03 (J03) unsafe proposals: another provider, a viewer, a developer, a consumer, a stale fence, a double click, an inconclusive verdict, a rolled-back release", async () => {
    const ref = await launch(3);
    assert.equal((await hook("step", { policy_ref: ref, report: "inconclusive" })).action, "hold");
    const fence = one(await mine(), ref).fence;
    const other: Actor = { providerId: w.B, role: "administrator" };
    assert.equal(reason(await as("other_dev").propose({ providerId: w.B, role: "developer" }, "rollback", ref, fence)), "not_found");
    assert.equal(reason(await as("other_dev").propose(admin, "rollback", ref, fence)), "not_found", "not a member of A");
    assert.deepEqual(value(await as("other_dev").releases({ ...other, role: "developer" })), { releases: [], decisions: [], proposals: [] });
    assert.equal(reason(await as("viewer").propose({ providerId: w.A, role: "viewer" }, "rollback", ref, fence)), "denied");
    assert.equal(reason(await as("dev").propose({ providerId: w.A, role: "developer" }, "rollback", ref, fence)), "denied");
    assert.equal(reason(await as("consumer").releases(admin)), "denied");
    assert.ok((await as("viewer").releases({ providerId: w.A, role: "viewer" })).ok, "every role reads");
    assert.equal(reason(await as("admin").propose(admin, "rollback", ref, fence - 1)), "conflict", "a stale revision");
    assert.equal(reason(await as("admin").propose(admin, "expand", ref, fence)), "conflict", "an inconclusive verdict never expands");
    assert.equal(reason(await as("admin").propose(admin, "rollback", ref, fence)), "ok");
    assert.equal(reason(await as("admin").propose(admin, "rollback", ref, fence)), "conflict", "a double click");
    assert.equal((await mine()).proposals.filter((p) => p.policyRef === ref).length, 1);
    const gone = await launch(4);
    await hook("step", { policy_ref: gone, errors: 21 });
    const g = one(await mine(), gone);
    for (const kind of ["rollback", "expand"] as const) assert.equal(reason(await as("admin").propose(admin, kind, gone, g.fence)), "conflict", kind);
  });

  await t.test("S04 (J04) an operator approval of a stale or rejected proposal leaves the release untouched", async () => {
    const ref = await launch(5);
    await hook("step", { policy_ref: ref, report: "accept" });
    const p = value(await as("admin").propose(admin, "expand", ref, one(await mine(), ref).fence));
    await hook("step", { policy_ref: ref, errors: 21 });
    assert.equal((await hook("decide", { proposal_id: p.proposalId, approve: true })).result, "conflict");
    const rec = await mine();
    assert.deepEqual([one(rec, ref).state, one(rec, ref).fence, rec.proposals.find((x) => x.proposalId === p.proposalId)!.state, rec.decisions.filter((d) => d.policyRef === ref).length], ["rolled_back", 2, "rejected", 1]);
    const other = await launch(6);
    await hook("step", { policy_ref: other, report: "accept" });
    const q = value(await as("admin").propose(admin, "expand", other, 1));
    assert.equal((await hook("decide", { proposal_id: q.proposalId, approve: false })).result, "ok");
    const r2 = await mine();
    assert.deepEqual([one(r2, other).state, one(r2, other).fence, r2.proposals.find((x) => x.proposalId === q.proposalId)!.state, r2.decisions.filter((d) => d.policyRef === other)], ["running", 1, "rejected", []]);
    const variants = value(await as("viewer").variants({ providerId: w.A, role: "viewer" }));
    assert.deepEqual(variants.map((v) => [v.changes, v.comparison]), [[["quantization:nvfp4"], null]], "the optimizations route reads through the same adapter");
  });
});
