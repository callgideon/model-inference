// L4's journeys J01/J02 (LAB-PUBLISH / LAB-ACCESS / CONSOLE-FLOWS), re-cut to L3 (E3L-F3): a rollback is
// the operator's listing decision, never a provider proposal (invalid); a registration is a model of the
// workspace with operator-imported weights, over one of those digests; a repeated proposal answers the
// open one (R214). One body for both worlds: journey.test.ts runs it on FakeControl, stack.test.ts through
// the HTTP adapter on the real control service (backend.py). What neither world shares (labels, ids, the
// seed's rows) is never asserted; App discovery (what a consumer's call is pinned to) is the listing's truth.
import assert from "node:assert/strict";
import type { Actor, ControlPort, Deployment, Registration } from "../../../lib/services/control/port.ts";

export type Who = "admin" | "dev" | "viewer" | "rival" | "rival_dev";
export type JourneyWorld = {
  A: string;
  B: string;
  /** The port as that user (the fake ignores it: its actor is the identity). */
  port(who: Who): ControlPort;
  /** A's model with imported weights: its bare name and one of those digests. */
  registration: Registration;
  /** The operator, outside the Lab: approve a proposal at a card. */
  approve(proposalId: string): Promise<void>;
  /** The operator rejects a proposal. The real L3 has none (E3L-F4): that world leaves it undefined. */
  reject?: (proposalId: string) => Promise<void>;
  /** The operator's rollback: the model's listing returns to its previous revision. */
  rollback(): Promise<void>;
  /** App discovery: the deployment revision a consumer's call to the model is pinned to. */
  discoverable(): Promise<string>;
};

const value = <T>(r: { ok: true; value: T } | { ok: false; reason: string }): T => {
  assert.ok(r.ok, `refused: ${!r.ok && r.reason}`);
  return r.value;
};
const reason = (r: { ok: boolean; reason?: string }) => (r.ok ? "ok" : r.reason);

export async function j01(w: JourneyWorld): Promise<void> {
  const admin: Actor = { providerId: w.A, role: "administrator" };
  const dev: Actor = { providerId: w.A, role: "developer" };
  const viewer: Actor = { providerId: w.A, role: "viewer" };
  const seed = await w.discoverable();
  const created = value(await w.port("dev").register(dev, w.registration));
  assert.deepEqual([created.environment, created.visibility, created.state, created.smoke, created.rateCardVersion], ["dev", "private", "active", "none", null]);
  assert.deepEqual([created.runtime, created.schemaVersion], [w.registration.runtime, w.registration.schemaVersion], "the registered identities are pinned");
  const models = value(await w.port("viewer").models(viewer));
  assert.ok(models.some((m) => m.modelId === created.modelId && m.revisionLabel === created.revisionLabel && m.artifactDigest === w.registration.artifactDigest), "the model revision is on record");
  assert.equal(await w.discoverable(), seed, "a dev revision is never discoverable");
  assert.equal(value(await w.port("dev").smoke(dev, created.deploymentRevisionId)).smoke, "passed");
  const p = value(await w.port("admin").propose(admin, "publish", created.deploymentRevisionId));
  assert.deepEqual([p.kind, p.state, p.deploymentRevisionId, p.decidedAt], ["publish", "proposed", created.deploymentRevisionId, null]);
  assert.equal(await w.discoverable(), seed, "a proposal publishes nothing");
  await w.approve(p.proposalId);
  const live = await w.discoverable();
  assert.notEqual(live, seed, "the operator's approval is what publishes");
  const listed = value(await w.port("viewer").deployments(viewer)).find((d) => d.deploymentRevisionId === live);
  assert.ok(listed !== undefined, "the discovered revision is on the Lab's records");
  assert.deepEqual([listed.modelId, listed.environment, listed.visibility, listed.state, listed.runtime], [created.modelId, "prod", "public", "active", w.registration.runtime]);
  assert.ok(listed.rateCardVersion !== null, "a published revision carries its rate card");
  const decided = value(await w.port("viewer").proposals(viewer)).filter((x) => x.deploymentRevisionId === created.deploymentRevisionId);
  assert.deepEqual(decided.map((x) => [x.proposalId, x.kind, x.state, x.decidedAt !== null]), [[p.proposalId, "publish", "approved", true]]);
  assert.equal(reason(await w.port("dev").smoke(dev, live)), "not_found", "smoke is a dev operation: a prod revision is no dev revision");
  assert.equal(reason(await w.port("admin").propose(admin, "rollback", live)), "invalid", "a rollback is the operator's, never a provider proposal");
  assert.equal(await w.discoverable(), live, "a refused rollback moves nothing");
  await w.rollback();
  assert.equal(await w.discoverable(), seed, "the operator's rollback restores the previous revision");
  const after = value(await w.port("viewer").deployments(viewer));
  assert.deepEqual(after.find((d) => d.deploymentRevisionId === created.deploymentRevisionId)?.environment, "dev", "the dev revision stays dev");
}

export async function j02(w: JourneyWorld): Promise<void> {
  const admin: Actor = { providerId: w.A, role: "administrator" };
  const dev: Actor = { providerId: w.A, role: "developer" };
  const viewer: Actor = { providerId: w.A, role: "viewer" };
  const rival: Actor = { providerId: w.B, role: "administrator" };
  const rivalDev: Actor = { providerId: w.B, role: "developer" };
  const seed = await w.discoverable();
  const reg = w.registration;
  assert.equal(reason(await w.port("viewer").register(viewer, reg)), "denied");
  assert.equal(reason(await w.port("dev").register(dev, { ...reg, name: "no-such-model" })), "not_found", "only a model of the workspace with imported weights");
  assert.equal(reason(await w.port("dev").register(dev, { ...reg, artifactDigest: `sha256:${"e".repeat(64)}` })), "invalid", "only over one of its imported digests");
  assert.equal(reason(await w.port("dev").register(dev, { ...reg, artifactDigest: "sha256:mutable" })), "invalid");
  assert.equal(reason(await w.port("rival").register(rival, reg)), "not_found", "another provider's model is not in this workspace");
  const d: Deployment = value(await w.port("dev").register(dev, reg));
  assert.equal(reason(await w.port("viewer").smoke(viewer, d.deploymentRevisionId)), "denied");
  assert.equal(reason(await w.port("viewer").smoke(viewer, "a5000000-0000-4000-8000-00000000ffff")), "denied", "the role first: an unknown id is not even looked up");
  assert.equal(reason(await w.port("rival").smoke(rival, d.deploymentRevisionId)), "not_found");
  assert.equal(reason(await w.port("rival_dev").smoke(rivalDev, d.deploymentRevisionId)), "not_found", "a foreign id confirms nothing, whatever the role");
  assert.equal(reason(await w.port("rival").propose(rival, "publish", d.deploymentRevisionId)), "not_found");
  assert.equal(reason(await w.port("rival_dev").propose(rivalDev, "publish", d.deploymentRevisionId)), "denied", "the role is judged in the caller's own workspace first: it confirms nothing about the id");
  assert.equal(reason(await w.port("admin").propose(admin, "publish", d.deploymentRevisionId)), "conflict", "no publish before a passed smoke");
  await w.port("dev").smoke(dev, d.deploymentRevisionId);
  assert.equal(reason(await w.port("dev").propose(dev, "publish", d.deploymentRevisionId)), "denied");
  assert.equal(reason(await w.port("dev").propose(dev, "rollback", d.deploymentRevisionId)), "denied", "the role before the kind");
  assert.equal(reason(await w.port("admin").propose(admin, "rollback", d.deploymentRevisionId)), "invalid", "a rollback is never a provider proposal");
  const p = value(await w.port("admin").propose(admin, "publish", d.deploymentRevisionId));
  const again = value(await w.port("admin").propose(admin, "publish", d.deploymentRevisionId));
  assert.deepEqual([again.proposalId, again.state], [p.proposalId, "proposed"], "a repeated proposal answers the open one (R214)");
  const ofD = async () => value(await w.port("viewer").proposals(viewer)).filter((x) => x.deploymentRevisionId === d.deploymentRevisionId);
  assert.deepEqual((await ofD()).map((x) => x.proposalId), [p.proposalId], "one proposal, not two");
  assert.equal(await w.discoverable(), seed, "an open proposal publishes nothing");
  if (w.reject !== undefined) {
    await w.reject(p.proposalId);
    assert.equal(await w.discoverable(), seed, "a rejected proposal publishes nothing");
    assert.deepEqual((await ofD()).map((x) => [x.proposalId, x.state]), [[p.proposalId, "rejected"]]);
  }
  const mine = new Set(value(await w.port("viewer").deployments(viewer)).map((x) => x.deploymentRevisionId));
  const theirs = w.port("rival");
  const reads = { deployments: await theirs.deployments(rival), models: await theirs.models(rival), proposals: await theirs.proposals(rival), aggregates: await theirs.aggregates(rival) };
  for (const [read, answer] of Object.entries(reads)) assert.deepEqual(value<unknown[]>(answer), [], `another provider sees none of it (${read})`);
  const health = value(await w.port("viewer").aggregates(viewer));
  assert.ok(health.length > 0 && health.every((a) => mine.has(a.deploymentRevisionId)), "every role reads its own aggregate health");
}
