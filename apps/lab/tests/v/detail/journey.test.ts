// V2 TRACE-TENANT on the fake that stands in for lab-api's trace read (WR-V2-1): the rules the real
// adapter must keep, driven through the same content state the page renders.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeTraces } from "../../../components/traces/detail/fake.ts";
import type { Actor, TraceDetail } from "../../../components/traces/detail/port.ts";
import { TRACE_COPY } from "../../../components/traces/detail/view.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const B = "b0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const META = "5c000000-0000-4000-8000-0000000000f2";
const dev = (providerId: string): Actor => ({ providerId, role: "developer" });
const base = {
  started_at: "2026-09-27T10:00:00Z", completed_at: "2026-09-27T10:00:01Z", mode: "full", loss_reason: "none", serving_version_id: "sv-1",
  model_revision: "acme-7b@r2", rate_card_version: null, policy_version: null, model_id: "acme-7b", price_version: "pv-1",
  request_schema_version: 2, elapsed_ms: 1000,
};
const granted: TraceDetail = {
  ...base, request_id: REQ, access: "content", access_state: "content", grantor_org_id: "0a000000-0000-4000-8000-00000000000a", grant_ref: "grant-1",
  content_complete: true, content_bytes: 30, content_available: true,
};

function world() {
  const fake = new FakeTraces();
  fake.add(A, granted);
  fake.add(A, { ...base, request_id: META, access: "metadata", access_state: "metadata" });
  return { fake };
}

test("V2-J01 the provider's developer reads its own request; a granted one is available, an ungranted one metadata only", async () => {
  const { fake } = world();
  const detail = await fake.detail(dev(A), REQ);
  assert.ok(detail.ok);
  assert.equal(detail.value.access_state, "content");
  const meta = await fake.detail(dev(A), META);
  assert.ok(meta.ok);
  assert.equal(meta.value.access_state, "metadata");
});

test("V2-J02 another provider, a viewer and a malformed id learn nothing; a missing request is the not-projected copy", async () => {
  const { fake } = world();
  assert.deepEqual(await fake.detail(dev(B), REQ), { ok: false, reason: "not_found" });
  assert.deepEqual(await fake.detail({ providerId: A, role: "viewer" }, REQ), { ok: false, reason: "denied" });
  for (const id of [REQ.toUpperCase(), `${REQ} `, "../5c000000", ""]) assert.deepEqual(await fake.detail(dev(A), id), { ok: false, reason: "not_found" }, id);
  assert.ok(TRACE_COPY.not_found.length > 0);
});
