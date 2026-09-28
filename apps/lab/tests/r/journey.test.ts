// R4 journey (ROLLOUT-PIN / CONSOLE-FLOWS) over the releases port. Until the /lab/v1/releases route
// lands (WR-R4-1) it runs on FakeReleases, the executable shape of that request. `step` stands in for
// R2's controller pass and `decide` for the operator's R2 approve/emergency_rollback: the Lab calls neither.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeReleases } from "../../lib/services/rollouts/fake.ts";
import type { Actor } from "../../lib/services/rollouts/port.ts";
import { releaseRows } from "../../lib/services/rollouts/view.ts";
import { A, B, BASE, EXPAND, POLICY, release, RUNS, T } from "./fixtures.ts";

const admin: Actor = { providerId: A, role: "administrator" };
const OPERATOR = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const value = <T>(r: { ok: true; value: T } | { ok: false; reason: string }): T => {
  assert.ok(r.ok, `refused: ${!r.ok && r.reason}`);
  return r.value;
};
const reason = (r: { ok: boolean; reason?: string }) => (r.ok ? "ok" : r.reason);
const seeded = (over = {}) => {
  const fake = new FakeReleases();
  fake.seed(A, release(over));
  return fake;
};
const mine = async (fake: FakeReleases, actor: Actor = admin) => value(await fake.releases(actor));

test("R4-J01 guardrail rollback: the controller's rollback verdict is one D9 transition and the page shows it from the records", async () => {
  const fake = seeded();
  const breach = { action: "rollback" as const, reasons: ["error_rate"], evidenceRefs: [], evaluatedAt: T };
  fake.step(POLICY, breach);
  fake.step(POLICY, breach);
  const rec = await mine(fake);
  assert.deepEqual([rec.releases[0].state, rec.releases[0].fence], ["rolled_back", 4], "a breach rolls back exactly once");
  assert.equal(rec.decisions.length, 1);
  const [row] = releaseRows("administrator", rec);
  assert.equal(row.status, `rolled back · serving returns to ${BASE}`);
  assert.deepEqual(row.lineage, [`rollback by controller at ${T}: error_rate`]);
  assert.deepEqual(row.actions, []);
  fake.step(POLICY, EXPAND);
  assert.equal((await mine(fake)).releases[0].state, "rolled_back", "a rolled-back release stays rolled back");
});

test("R4-J02 promotion: expand verdict → administrator proposal → operator approval → approved with its evidence", async () => {
  const fake = seeded({ verdict: EXPAND });
  const p = value(await fake.propose(admin, "expand", POLICY, 3));
  assert.equal((await mine(fake)).releases[0].state, "running", "a proposal changes nothing");
  assert.deepEqual(releaseRows("administrator", await mine(fake))[0].pending, "expand proposed · awaiting operator approval");
  assert.equal(fake.decide(p.proposalId, true, OPERATOR), "ok");
  const rec = await mine(fake);
  assert.deepEqual([rec.releases[0].state, rec.proposals[0].state], ["approved", "approved"]);
  assert.deepEqual(rec.decisions, [{ policyRef: POLICY, decision: "expand", reasons: [], evidenceRefs: RUNS, decidedBy: OPERATOR, decidedAt: rec.decisions[0].decidedAt }]);
  const back = value(await fake.propose(admin, "rollback", POLICY, 4));
  assert.equal(fake.decide(back.proposalId, true, OPERATOR), "ok");
  const after = await mine(fake);
  assert.deepEqual([after.releases[0].state, after.decisions.at(-1)!.decision, after.decisions.at(-1)!.reasons], ["rolled_back", "rollback", ["operator:proposal"]]);
});

test("R4-J03 unsafe proposals: another provider, a viewer or developer, a stale fence, a double click, an inconclusive verdict, a rolled-back release", async () => {
  const fake = seeded();
  assert.equal(reason(await fake.propose({ providerId: B, role: "administrator" }, "rollback", POLICY, 3)), "not_found");
  assert.equal(reason(await fake.propose({ providerId: B, role: "viewer" }, "rollback", POLICY, 3)), "not_found", "existence is not confirmed by role");
  assert.deepEqual(await mine(fake, { providerId: B, role: "administrator" }), { releases: [], decisions: [], proposals: [] });
  assert.equal(reason(await fake.propose({ providerId: A, role: "viewer" }, "rollback", POLICY, 3)), "denied");
  assert.equal(reason(await fake.propose({ providerId: A, role: "developer" }, "rollback", POLICY, 3)), "denied");
  assert.equal(reason(await fake.propose(admin, "rollback", POLICY, 2)), "conflict", "a stale revision");
  assert.equal(reason(await fake.propose(admin, "expand", POLICY, 3)), "conflict", "an inconclusive verdict never expands");
  assert.equal(reason(await fake.propose(admin, "rollback", POLICY, 3)), "ok");
  assert.equal(reason(await fake.propose(admin, "rollback", POLICY, 3)), "conflict", "a double click");
  assert.equal((await mine(fake)).proposals.length, 1);
  const gone = seeded({ state: "rolled_back", verdict: EXPAND });
  assert.equal(reason(await gone.propose(admin, "rollback", POLICY, 3)), "conflict");
  assert.equal(reason(await gone.propose(admin, "expand", POLICY, 3)), "conflict");
  const up = seeded({ state: "approved", verdict: EXPAND });
  assert.equal(reason(await up.propose(admin, "expand", POLICY, 3)), "conflict", "an approved release is not expanded twice");
});

test("R4-J04 an operator approval of a stale or rejected proposal leaves the release untouched", async () => {
  const fake = seeded({ verdict: EXPAND });
  const p = value(await fake.propose(admin, "expand", POLICY, 3));
  fake.step(POLICY, { action: "rollback", reasons: ["latency"], evidenceRefs: [], evaluatedAt: T });
  assert.equal(fake.decide(p.proposalId, true, OPERATOR), "conflict");
  const rec = await mine(fake);
  assert.deepEqual([rec.releases[0].state, rec.releases[0].fence, rec.proposals[0].state, rec.decisions.length], ["rolled_back", 4, "rejected", 1]);
  const other = seeded({ verdict: EXPAND });
  const q = value(await other.propose(admin, "expand", POLICY, 3));
  assert.equal(other.decide(q.proposalId, false, OPERATOR), "ok");
  const r2 = await mine(other);
  assert.deepEqual([r2.releases[0].state, r2.releases[0].fence, r2.proposals[0].state, r2.decisions], ["running", 3, "rejected", []]);
});
