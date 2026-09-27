// V2 TRACE-TENANT on the fake that stands in for lab-api's trace read and C2 (WR-V2-1/2): the rules
// the real adapters must keep, driven through the same view the page renders.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeTraces } from "../../../components/traces/detail/fake.ts";
import type { Actor, TraceDetail } from "../../../components/traces/detail/port.ts";
import { contentView, TRACE_COPY } from "../../../components/traces/detail/view.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const B = "b0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const META = "5c000000-0000-4000-8000-0000000000f2";
const dev = (providerId: string): Actor => ({ providerId, role: "developer" });
const base = {
  started_at: "2026-09-27T10:00:00Z", completed_at: "2026-09-27T10:00:01Z", mode: "full", loss_reason: "none", serving_version_id: "sv-1",
  model_revision: "acme-7b@r2", rate_card_version: null, policy_version: null, model_id: "acme-7b",
};
const granted: TraceDetail = {
  ...base, request_id: REQ, access: "content", grantor_org_id: "0a000000-0000-4000-8000-00000000000a", grant_ref: "grant-1",
  content_complete: true, content_bytes: 30, content_available: true,
};

function world() {
  const fake = new FakeTraces();
  const row = fake.add(A, granted, "Provider-owned fixture prompt.");
  fake.add(A, { ...base, request_id: META, access: "metadata" }, "never shown");
  return { fake, row };
}

test("V2-J01 the provider's developer reads its own request and opens its content on demand", async () => {
  const { fake } = world();
  const detail = await fake.detail(dev(A), REQ);
  assert.ok(detail.ok);
  const view = await contentView(fake, dev(A), detail.value, true);
  assert.deepEqual([view.state, view.text], ["available", "Provider-owned fixture prompt."]);
});

test("V2-J02 another provider, a viewer and a malformed id learn nothing; a missing request is the not-projected copy", async () => {
  const { fake } = world();
  assert.deepEqual(await fake.detail(dev(B), REQ), { ok: false, reason: "not_found" });
  assert.deepEqual(await fake.detail({ providerId: A, role: "viewer" }, REQ), { ok: false, reason: "denied" });
  for (const id of [REQ.toUpperCase(), `${REQ} `, "../5c000000", ""]) assert.deepEqual(await fake.detail(dev(A), id), { ok: false, reason: "not_found" }, id);
  assert.deepEqual(await fake.read(dev(B), "grant-1", REQ), { ok: false, reason: "not_found" });
  assert.deepEqual(await fake.read({ providerId: A, role: "viewer" }, "grant-1", REQ), { ok: false, reason: "not_found" });
  assert.ok(TRACE_COPY.not_found.length > 0);
});

test("V2-J03 without a current grant, after revocation, with a forged grant or past T3 retention, no content is read", async () => {
  const { fake, row } = world();
  const meta = await fake.detail(dev(A), META);
  assert.ok(meta.ok);
  assert.equal((await contentView(fake, dev(A), meta.value, true)).state, "metadata_only");
  assert.deepEqual(await fake.read(dev(A), "grant-1", META), { ok: false, reason: "forbidden" });
  assert.deepEqual(await fake.read(dev(A), "grant-forged", REQ), { ok: false, reason: "forbidden" });
  row.revoked = true;
  assert.deepEqual([(await contentView(fake, dev(A), granted, true)).state], ["revoked"]);
  row.revoked = false;
  row.refExpired = true;
  assert.deepEqual((await contentView(fake, dev(A), granted, true)).state, "expired");
  row.refExpired = false;
  row.detail = { ...granted, content_available: false };
  assert.deepEqual(await fake.read(dev(A), "grant-1", REQ), { ok: false, reason: "expired" });
});
