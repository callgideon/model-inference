// L1 LAB-ACCESS: who gets a provider workspace. Memberships come only from the L2 read (the
// user's own session, no client-supplied identity); a cookie is a preference, never authorization.
import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCESS_COPY,
  chooseWorkspace,
  providerAccess,
  readyWorkspace,
  resolveAccess,
  sessionAccess,
  type Membership,
} from "../../../lib/auth/access.ts";
import { MEMBERSHIPS_RPC, parseMemberships, readMemberships } from "../../../lib/auth/memberships.ts";

const A: Membership = { providerId: "11111111-1111-4111-8111-111111111111", providerName: "Acme", role: "developer" };
const B: Membership = { providerId: "22222222-2222-4222-8222-222222222222", providerName: "Beta", role: "viewer" };
const OTHER = "33333333-3333-4333-8333-333333333333";
const row = (m: Membership) => ({ provider_org_id: m.providerId, provider_name: m.providerName, role: m.role });

test("L1-A01 a signed-in user with no provider membership (consumer-only) is denied", () => {
  assert.deepEqual(providerAccess({ ok: true, memberships: [] }, undefined), { kind: "denied" });
  assert.deepEqual(providerAccess({ ok: true, memberships: [] }, A.providerId), { kind: "denied" });
});

test("L1-A02 a single membership is selected without asking", () => {
  assert.deepEqual(providerAccess({ ok: true, memberships: [A] }, undefined), { kind: "ready", workspace: A, workspaces: [A] });
});

test("L1-A03 several memberships and no valid choice ask the user to select", () => {
  assert.deepEqual(providerAccess({ ok: true, memberships: [A, B] }, undefined), { kind: "select", workspaces: [A, B] });
  assert.equal(providerAccess({ ok: true, memberships: [A, B] }, B.providerId).kind, "ready");
  const chosen = providerAccess({ ok: true, memberships: [A, B] }, B.providerId);
  assert.equal(chosen.kind === "ready" && chosen.workspace, B);
});

test("L1-A04 a cookie naming a provider the user is not a member of selects nothing", () => {
  assert.deepEqual(providerAccess({ ok: true, memberships: [A, B] }, OTHER), { kind: "select", workspaces: [A, B] });
  assert.deepEqual(providerAccess({ ok: true, memberships: [A] }, OTHER), { kind: "ready", workspace: A, workspaces: [A] });
});

test("L1-A05 a failed membership read is unavailable, never denied and never a workspace", () => {
  assert.deepEqual(providerAccess({ ok: false }, A.providerId), { kind: "unavailable" });
});

test("L1-A06 a signed-out request is signed-out and never reads memberships", async () => {
  let reads = 0;
  const access = await resolveAccess({
    userId: async () => null,
    memberships: async () => {
      reads += 1;
      return { ok: true, memberships: [A] };
    },
    selected: A.providerId,
  });
  assert.deepEqual(access, { kind: "signed-out" });
  assert.equal(reads, 0);
  const signedIn = await resolveAccess({ userId: async () => "u1", memberships: async () => ({ ok: true, memberships: [A] }), selected: undefined });
  assert.equal(signedIn.kind, "ready");
});

test("L1-A07 the selection action accepts only one of the user's own workspaces", () => {
  const select = providerAccess({ ok: true, memberships: [A, B] }, undefined);
  assert.equal(chooseWorkspace(select, B.providerId), B);
  assert.equal(chooseWorkspace(select, OTHER), null);
  assert.equal(chooseWorkspace(select, null), null);
  assert.equal(chooseWorkspace({ kind: "denied" }, A.providerId), null);
  assert.equal(chooseWorkspace({ kind: "signed-out" }, A.providerId), null);
});

test("L1-A09 a page needs a selected workspace; the selection action needs a provider session", () => {
  const ready = providerAccess({ ok: true, memberships: [A] }, undefined);
  const select = providerAccess({ ok: true, memberships: [A, B] }, undefined);
  assert.equal(readyWorkspace(ready), A);
  assert.equal(readyWorkspace(select), null);
  assert.equal(sessionAccess(ready), ready);
  assert.equal(sessionAccess(select), select);
  for (const kind of ["signed-out", "unavailable", "denied"] as const) {
    assert.equal(readyWorkspace({ kind }), null);
    assert.equal(sessionAccess({ kind }), null);
  }
});

test("L1-A08 the denial copy keeps consumer onboarding in the App: no signup, credits or onboarding offer", () => {
  for (const text of Object.values(ACCESS_COPY)) {
    assert.doesNotMatch(text, /credit|sign ?up|onboard|welcome|grant/i);
  }
  assert.match(ACCESS_COPY.denied, /no provider workspace/i);
  assert.match(ACCESS_COPY.unavailable, /could not be checked/i);
});

test("L1-M01 the membership read is the named RPC over the user's own session, with no identity argument", async () => {
  const calls: unknown[][] = [];
  const client = {
    rpc: async (...args: unknown[]) => {
      calls.push(args);
      return { data: [row(A)], error: null };
    },
  };
  assert.deepEqual(await readMemberships(client), { ok: true, memberships: [A] });
  assert.deepEqual(calls, [[MEMBERSHIPS_RPC]]);
  assert.equal(MEMBERSHIPS_RPC, "lab_my_provider_memberships");
});

test("L1-M02 an RPC error or a thrown transport is unavailable", async () => {
  assert.deepEqual(await readMemberships({ rpc: async () => ({ data: null, error: { message: "x" } }) }), { ok: false });
  assert.deepEqual(await readMemberships({ rpc: async () => { throw new Error("down"); } }), { ok: false });
});

test("L1-M03 a malformed, unknown-role or duplicate row fails the whole read closed", () => {
  assert.deepEqual(parseMemberships([row(A), row(B)]), { ok: true, memberships: [A, B] });
  assert.deepEqual(parseMemberships([]), { ok: true, memberships: [] });
  assert.deepEqual(parseMemberships(null), { ok: false });
  assert.deepEqual(parseMemberships([row(A), { ...row(B), role: "owner" }]), { ok: false });
  assert.deepEqual(parseMemberships([row(A), { ...row(B), provider_org_id: "not-a-uuid" }]), { ok: false });
  assert.deepEqual(parseMemberships([{ ...row(A), provider_name: "" }]), { ok: false });
  assert.deepEqual(parseMemberships([row(A), row(A)]), { ok: false });
});
