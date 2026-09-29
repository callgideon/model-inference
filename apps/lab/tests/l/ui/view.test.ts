// L4: what a provider sees and may do, derived only from control records (never button-local state).
import assert from "node:assert/strict";
import test from "node:test";
import { controlPort, holds, isPreview, type Aggregate, type Deployment, type Proposal } from "../../../lib/services/control/port.ts";
import { deploymentRows, healthRows, refusalCopy, REFUSAL_COPY } from "../../../lib/services/control/view.ts";

const dep = (over: Partial<Deployment>): Deployment => ({
  deploymentRevisionId: "d-dev", modelId: "acme-7b", servingVersionId: "sv-1", revisionLabel: "r1", runtime: "vllm@sha256:aa",
  schemaVersion: "chat.v2", rateCardVersion: null, environment: "dev", visibility: "private", state: "active", smoke: "none",
  createdAt: "2026-09-27T10:00:00Z", ...over,
});
const prod = dep({ deploymentRevisionId: "d-prod", environment: "prod", visibility: "public", rateCardVersion: "rc-3", smoke: "passed" });
const proposal = (over: Partial<Proposal>): Proposal => ({
  proposalId: "p1", kind: "publish", deploymentRevisionId: "d-dev", state: "proposed", proposedAt: "2026-09-27T10:05:00Z", decidedAt: null, ...over,
});

test("L4-V01 roles mirror ROLE_CAPABILITIES: viewer reads health, developer manages dev, only administrator proposes", () => {
  const caps = ["read_aggregate_health", "manage_dev_deployment", "propose_publication"] as const;
  const table = (["viewer", "developer", "administrator"] as const).map((r) => caps.map((c) => holds(r, c)));
  assert.deepEqual(table, [[true, false, false], [true, true, false], [true, true, true]]);
});

test("L4-V02 a row shows the pinned model, serving, runtime, schema and rate identities and where it is visible", () => {
  const [dev, live] = deploymentRows("viewer", [dep({}), prod], []);
  assert.deepEqual(
    [dev.model, dev.serving, dev.runtime, dev.schema, dev.rate, dev.where],
    ["acme-7b", "sv-1@r1", "vllm@sha256:aa", "chat.v2", "unpriced", "dev · private"],
  );
  assert.deepEqual([live.rate, live.where], ["rc-3", "prod · public"]);
});

test("L4-V03 actions follow the record and the role: smoke dev, publish only after a passed smoke, never a provider rollback", () => {
  const acts = (role: "viewer" | "developer" | "administrator", d: Deployment, p: Proposal[] = []) => deploymentRows(role, [d], p)[0].actions;
  assert.deepEqual(acts("viewer", dep({})), []);
  assert.deepEqual(acts("developer", dep({})), ["smoke"]);
  assert.deepEqual(acts("developer", dep({ smoke: "passed" })), ["smoke"]);
  assert.deepEqual(acts("administrator", dep({ smoke: "passed" })), ["smoke", "publish"]);
  assert.deepEqual(acts("administrator", dep({ smoke: "failed" })), ["smoke"]);
  assert.deepEqual(acts("administrator", dep({ smoke: "passed", state: "retired" })), []);
  assert.deepEqual(acts("administrator", prod), [], "a rollback is the operator's listing decision, never offered to a provider (E3L-F3)");
  assert.deepEqual(acts("developer", prod), []);
  assert.deepEqual(acts("administrator", dep({ ...prod, visibility: "private" })), []);
});

test("L4-V04 a pending proposal is shown as awaiting the operator and blocks a second one; a decided one does not", () => {
  const [row] = deploymentRows("administrator", [dep({ smoke: "passed" })], [proposal({})]);
  assert.equal(row.pending, "publish proposed · awaiting operator approval");
  assert.deepEqual(row.actions, ["smoke"]);
  const [other] = deploymentRows("administrator", [dep({ smoke: "passed" })], [proposal({ deploymentRevisionId: "d-other" })]);
  assert.deepEqual([other.pending, other.actions], [null, ["smoke", "publish"]]);
  const [rejected] = deploymentRows("administrator", [dep({ smoke: "passed" })], [proposal({ state: "rejected", decidedAt: "2026-09-27T11:00:00Z" })]);
  assert.deepEqual([rejected.pending, rejected.actions], [null, ["smoke", "publish"]]);
});

test("L4-V05 health rows carry the redacted aggregate only, whatever else a record carries", () => {
  const leaky = {
    deploymentRevisionId: "d-prod", windowStart: "2026-09-27T09:00:00Z", windowEnd: "2026-09-27T10:00:00Z", requests: 200, errors: 3,
    p95LatencyMs: 812, user_id: "u-customer", org_id: "o-customer", prompt: "secret prompt",
  } as Aggregate;
  const [row] = healthRows([leaky, { ...leaky, requests: 0, errors: 0, p95LatencyMs: null }]).slice(0, 1);
  assert.deepEqual(row, { deployment: "d-prod", window: "2026-09-27T09:00:00Z – 2026-09-27T10:00:00Z", requests: "200", errorRate: "1.5%", p95: "812 ms" });
  const [, idle] = healthRows([leaky, { ...leaky, requests: 0, errors: 0, p95LatencyMs: null }]);
  assert.deepEqual([idle.errorRate, idle.p95], ["—", "—"]);
  assert.doesNotMatch(JSON.stringify(healthRows([leaky])), /customer|secret/);
});

test("L4-V06 a refusal is fixed copy for a known reason and nothing for anything else in the URL", () => {
  assert.equal(refusalCopy("denied"), REFUSAL_COPY.denied);
  assert.equal(refusalCopy("conflict"), REFUSAL_COPY.conflict);
  for (const junk of [undefined, "", "<script>", "toString", ["denied"]]) assert.equal(refusalCopy(junk), null);
  assert.doesNotMatch(Object.values(REFUSAL_COPY).join(" "), /success|published|done/i);
});

test("L4-V07 the control port fails closed: unavailable until the real adapter is wired, the preview never in production", async () => {
  const actor = { providerId: "11111111-1111-4111-8111-111111111111", role: "administrator" as const };
  for (const env of [{}, { NODE_ENV: "production", LAB_CONTROL_PREVIEW: "1" }, { LAB_CONTROL_PREVIEW: "true" }]) {
    const port = controlPort(env);
    assert.equal(isPreview(env), false);
    for (const answer of [await port.deployments(actor), await port.register(actor, { name: "x", artifactDigest: "d", schemaVersion: "s", runtime: "r" })])
      assert.deepEqual(answer, { ok: false, reason: "unavailable" });
  }
  assert.equal(isPreview({ NODE_ENV: "development", LAB_CONTROL_PREVIEW: "1" }), true);
  assert.deepEqual(await controlPort({ LAB_CONTROL_PREVIEW: "1" }).deployments(actor), { ok: true, value: [] });
});
