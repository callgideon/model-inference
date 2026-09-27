// L4 journey (LAB-PUBLISH / LAB-ACCESS / CONSOLE-FLOWS) over the control port. Until L3 merges it
// runs on FakeControl, the executable shape filed as WR-L4-1; the coordinator reruns it on the real
// adapter at merge. `decide` and `discoverable` stand in for the operator's boundary and App discovery.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeControl } from "../../../lib/services/control/fake.ts";
import type { Actor } from "../../../lib/services/control/port.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const admin: Actor = { providerId: A, role: "administrator" };
const dev: Actor = { providerId: A, role: "developer" };
const viewer: Actor = { providerId: A, role: "viewer" };
const rival: Actor = { providerId: B, role: "administrator" };
const reg = (name: string, digest = "a") => ({ name, artifactDigest: `sha256:${digest.repeat(64)}`, schemaVersion: "chat.v2", runtime: "vllm@sha256:" + "b".repeat(64) });
const value = <T>(r: { ok: true; value: T } | { ok: false; reason: string }): T => {
  assert.ok(r.ok, `refused: ${!r.ok && r.reason}`);
  return r.value;
};
const reason = (r: { ok: boolean; reason?: string }) => (r.ok ? "ok" : r.reason);

test("L4-J01 register → dev smoke → publish proposal → operator approval → App discovery → rollback", async () => {
  const control = new FakeControl();
  const first = value(await control.register(dev, reg("acme-7b")));
  assert.deepEqual([first.environment, first.visibility, first.smoke, first.rateCardVersion], ["dev", "private", "none", null]);
  assert.deepEqual(control.discoverable(), [], "a dev revision is never discoverable");
  assert.equal(value(await control.smoke(dev, first.deploymentRevisionId)).smoke, "passed");
  const p1 = value(await control.propose(admin, "publish", first.deploymentRevisionId));
  assert.equal(p1.state, "proposed");
  assert.deepEqual(control.discoverable(), [], "a proposal publishes nothing");
  control.decide(p1.proposalId, true);
  assert.deepEqual(control.discoverable(), ["acme-7b@r1"]);
  const firstLive = value(await control.deployments(viewer)).find((d) => d.environment === "prod")!;
  assert.equal(reason(await control.smoke(dev, firstLive.deploymentRevisionId)), "conflict", "smoke is a dev operation");
  assert.equal(reason(await control.propose(admin, "rollback", firstLive.deploymentRevisionId)), "conflict", "nothing earlier to roll back to");

  const second = value(await control.register(dev, reg("acme-7b", "c")));
  await control.smoke(dev, second.deploymentRevisionId);
  control.decide(value(await control.propose(admin, "publish", second.deploymentRevisionId)).proposalId, true);
  assert.deepEqual(control.discoverable(), ["acme-7b@r2"], "one public revision per model");
  const live = value(await control.deployments(viewer)).find((d) => d.environment === "prod" && d.visibility === "public");
  assert.ok(live !== undefined && live.rateCardVersion !== null, "a published revision carries its rate card");
  control.decide(value(await control.propose(admin, "rollback", live.deploymentRevisionId)).proposalId, true);
  assert.deepEqual(control.discoverable(), ["acme-7b@r1"], "rollback restores the previous public revision");
  const states = value(await control.proposals(viewer)).map((p) => `${p.kind}:${p.state}`);
  assert.deepEqual(states, ["publish:approved", "publish:approved", "rollback:approved"]);
});

test("L4-J02 unauthorized variants: another provider, a viewer, a developer proposing, publish before smoke, a duplicate", async () => {
  const control = new FakeControl();
  const d = value(await control.register(dev, reg("acme-7b")));
  assert.equal(reason(await control.register(viewer, reg("x"))), "denied");
  assert.equal(reason(await control.smoke(viewer, d.deploymentRevisionId)), "denied");
  assert.equal(reason(await control.smoke(rival, d.deploymentRevisionId)), "not_found");
  assert.equal(reason(await control.smoke({ providerId: B, role: "viewer" }, d.deploymentRevisionId)), "not_found", "a foreign id confirms nothing, whatever the role");
  assert.equal(reason(await control.propose(rival, "publish", d.deploymentRevisionId)), "not_found");
  assert.equal(reason(await control.propose(admin, "publish", d.deploymentRevisionId)), "conflict", "no publish before a passed smoke");
  await control.smoke(dev, d.deploymentRevisionId);
  assert.equal(reason(await control.propose(dev, "publish", d.deploymentRevisionId)), "denied");
  assert.equal(reason(await control.propose(admin, "rollback", d.deploymentRevisionId)), "conflict", "a dev revision has nothing to roll back");
  const p = value(await control.propose(admin, "publish", d.deploymentRevisionId));
  assert.equal(reason(await control.propose(admin, "publish", d.deploymentRevisionId)), "conflict", "one pending proposal per revision");
  control.decide(p.proposalId, false);
  assert.deepEqual(control.discoverable(), [], "a rejected proposal publishes nothing");
  assert.deepEqual(value(await control.deployments(rival)), [], "another provider sees none of it");
  assert.deepEqual(value(await control.models(rival)), []);
  assert.deepEqual(value(await control.proposals(rival)), []);
  assert.equal(reason(await control.register(dev, { ...reg("y"), artifactDigest: "sha256:mutable" })), "invalid");
  const window = { deploymentRevisionId: d.deploymentRevisionId, windowStart: "2026-09-27T09:00:00Z", windowEnd: "2026-09-27T10:00:00Z", requests: 5, errors: 0, p95LatencyMs: 90 };
  control.observe(A, window);
  assert.deepEqual(value(await control.aggregates(viewer)), [window], "every role reads its own aggregate health");
  assert.deepEqual(value(await control.aggregates(rival)), []);
});
