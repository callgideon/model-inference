// C2 LAB-ACCESS / CONSOLE-FLOWS: the Lab's content-ref adapter. The database decides (grant,
// recipient, expiry, retention); the adapter sends only the handle's digest and fails closed.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { isDeepStrictEqual } from "node:util";
import {
  CONTENT_COPY,
  ISSUE_RPC,
  contentMessage,
  issueContentRef,
  refusalOf,
  type RpcClient,
} from "../../../lib/services/content/refs.ts";

const PROVIDER = "a0000000-0000-4000-8000-00000000000a";
const GRANTOR = "c1000000-0000-4000-8000-0000000000c1";
const REQUEST = "4d4d4d4d-0000-4000-8000-000000000001";
const INPUT = { providerOrgId: PROVIDER, grantRef: `lab:grant:${PROVIDER}:x@sha256:${"a".repeat(64)}`, requestId: REQUEST, purpose: "provider_sharing" as const };
const ROW = { grantor_org_id: GRANTOR, request_id: REQUEST, expires_at: "2026-09-27T12:05:00Z", grant_version: 1 };

// One-line failure text: node's multi-line diff would hide the assertion code from the mutant judge.
const same = (actual: unknown, expected: unknown) => assert.ok(isDeepStrictEqual(actual, expected), JSON.stringify(actual));

function client(answer: () => { data: unknown; error: unknown }) {
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const rpc: RpcClient = { rpc: async (name, args) => (calls.push({ name, args }), answer()) };
  return { rpc, calls };
}

test("C2-L01 the issue is the named RPC with the handle's digest only, never the handle or an identity", async () => {
  const { rpc, calls } = client(() => ({ data: ROW, error: null }));
  const issued = await issueContentRef(rpc, INPUT);
  assert.equal(issued.ok, true);
  if (!issued.ok) return;
  assert.match(issued.handle, /^tc_[A-Za-z0-9_-]{43}$/);
  same(calls, [{ name: ISSUE_RPC, args: {
    p_handle_sha256: createHash("sha256").update(issued.handle).digest("hex"),
    p_provider_org_id: PROVIDER, p_grant_ref: INPUT.grantRef, p_request_id: REQUEST, p_purpose: "provider_sharing",
  } }]);
  same({ ...issued, handle: "" }, { ok: true, handle: "", grantorOrgId: GRANTOR, requestId: REQUEST, expiresAt: ROW.expires_at });
  const again = await issueContentRef(rpc, INPUT);
  assert.ok(again.ok && again.handle !== issued.handle);
});

test("C2-L02 a refusal maps by its code; anything else, or a throw, is unavailable", async () => {
  for (const [message, reason] of [["not_found: no such request", "not_found"], ["forbidden: no grant", "forbidden"],
    ["result_expired: past retention", "expired"], ["state_conflict: reused", "unavailable"], ["constructor: x", "unavailable"]]) {
    const { rpc } = client(() => ({ data: null, error: { message } }));
    same(await issueContentRef(rpc, INPUT), { ok: false, reason });
  }
  assert.equal(refusalOf(null), "unavailable");
  const thrown: RpcClient = { rpc: async () => { throw new Error("network"); } };
  same(await issueContentRef(thrown, INPUT).catch(() => "escaped"), { ok: false, reason: "unavailable" });
});

test("C2-L03 a malformed answer fails closed", async () => {
  for (const data of [null, { ...ROW, grantor_org_id: "x" }, { ...ROW, request_id: GRANTOR }, { ...ROW, expires_at: "soon" }, { ...ROW, expires_at: 1 }]) {
    const { rpc } = client(() => ({ data, error: null }));
    same(await issueContentRef(rpc, INPUT), { ok: false, reason: "unavailable" });
  }
});

test("C2-L04 a malformed input never reaches the RPC", async () => {
  const { rpc, calls } = client(() => ({ data: ROW, error: null }));
  same(await issueContentRef(rpc, { ...INPUT, providerOrgId: "p" }), { ok: false, reason: "not_found" });
  same(await issueContentRef(rpc, { ...INPUT, requestId: "../trace/x" }), { ok: false, reason: "not_found" });
  // @ts-expect-error: an untyped form value
  same(await issueContentRef(rpc, { ...INPUT, purpose: "capture" }), { ok: false, reason: "forbidden" });
  assert.equal(calls.length, 0);
});

test("C2-L05 every content state reads as a sentence; an unknown one reads as lost", () => {
  const states = ["metadata_only", "pending", "lost", "expired", "off"];
  for (const state of states) assert.ok(contentMessage(state).length > 10, state);
  assert.equal(new Set(states.map(contentMessage)).size, states.length);
  assert.equal(contentMessage("available"), "");
  assert.equal(contentMessage("constructor"), CONTENT_COPY.lost);
  assert.equal(contentMessage("weird"), CONTENT_COPY.lost);
});
