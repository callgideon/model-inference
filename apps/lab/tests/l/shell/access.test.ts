// L1 LAB-ACCESS: who gets a provider workspace. Memberships come only from the API's read
// (GET /lab/v1/workspaces as the user's own session, no client-supplied identity); a cookie is a
// preference, never authorization.
import assert from "node:assert/strict";
import { realpathSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import {
  ACCESS_COPY,
  CAPABILITIES,
  chooseWorkspace,
  holds,
  providerAccess,
  readyWorkspace,
  resolveAccess,
  ROLE_CAPABILITIES,
  ROLES,
  sessionAccess,
  type Membership,
} from "../../../lib/auth/access.ts";
import { parseWorkspaces, readMemberships, workspaceFeatures } from "../../../lib/auth/memberships.ts";
import { land } from "../../../lib/services/common.ts";

const A: Membership = { providerId: "11111111-1111-4111-8111-111111111111", providerName: "Acme", role: "developer", capabilities: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"] };
const B: Membership = { providerId: "22222222-2222-4222-8222-222222222222", providerName: "Beta", role: "viewer", capabilities: ["read_aggregate_health"] };
const OTHER = "33333333-3333-4333-8333-333333333333";
const row = (m: Membership) => ({ provider_org_id: m.providerId, provider_name: m.providerName, role: m.role, capabilities: [...m.capabilities] });
const page = (...ms: Membership[]) => ({ data: ms.map(row), next_cursor: null });

type Sent = { method: string; url: string; auth: string | null; cookie: string | null; body: unknown };
/** The Lab port over a fake API answering `status`/`body` (or throwing), recording what was sent. */
function api(status: number, body: unknown, token: string | null = "eyJ.session.sig") {
  const sent: Sent[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const h = new Headers(init.headers);
    sent.push({ method: String(init.method), url: String(url), auth: h.get("authorization"), cookie: h.get("cookie"), body: init.body ?? null });
    if (status === 0) throw new Error("down");
    return new Response(JSON.stringify(body), { status });
  }) as typeof globalThis.fetch;
  return { api: labApi({ baseUrl: "https://lab-control.example", fetch, session: () => ({ token }) }), sent };
}

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

test("L1-A05 a failed membership read is unavailable, never denied and never a workspace; a dead session is signed-out", () => {
  assert.deepEqual(providerAccess({ ok: false, reason: "unavailable" }, A.providerId), { kind: "unavailable" });
  assert.deepEqual(providerAccess({ ok: false, reason: "signed-out" }, A.providerId), { kind: "signed-out" });
});

test("L1-A06 a signed-out request is signed-out and never reads memberships", async () => {
  let reads = 0;
  const memberships = async () => {
    reads += 1;
    return { ok: true as const, memberships: [A] };
  };
  assert.deepEqual(await resolveAccess({ signedIn: false, memberships, selected: A.providerId }), { kind: "signed-out" });
  assert.equal(reads, 0);
  const signedIn = await resolveAccess({ signedIn: true, memberships, selected: undefined });
  assert.equal(signedIn.kind, "ready");
  assert.equal(reads, 1);
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

test("L1-M01 the membership read is GET /lab/v1/workspaces as the user's own session, with no identity argument", async () => {
  const w = api(200, page(A));
  assert.deepEqual(await readMemberships(w.api), { ok: true, memberships: [A] });
  assert.deepEqual(w.sent, [{ method: "GET", url: "https://lab-control.example/lab/v1/workspaces", auth: "Bearer eyJ.session.sig", cookie: null, body: null }]);
});

test("L1-M02 an API failure or a thrown transport is unavailable; only a refused session (401) is signed-out", async () => {
  for (const [status, body] of [[503, { error: { code: "unavailable", message: "x", request_id: "r", retryable: true } }], [403, { refusal: "denied" }], [500, "x"], [0, null]] as const) {
    assert.deepEqual(await readMemberships(api(status, body).api), { ok: false, reason: "unavailable" }, String(status));
  }
  const dead = { error: { code: "unauthenticated", message: "Your session has ended.", request_id: "r", retryable: false } };
  assert.deepEqual(await readMemberships(api(401, dead).api), { ok: false, reason: "signed-out" });
});

test("L1-M03 a malformed, unknown-role or duplicate row fails the whole read closed; an unknown capability grants nothing", () => {
  const bad = { ok: false, reason: "unavailable" };
  assert.deepEqual(parseWorkspaces(page(A, B)), { ok: true, memberships: [A, B] });
  assert.deepEqual(parseWorkspaces(page()), { ok: true, memberships: [] });
  for (const body of [null, [], { data: null }, { data: [row(A), { ...row(B), role: "owner" }] }, { data: [row(A), { ...row(B), provider_org_id: "not-a-uuid" }] },
    { data: [{ ...row(A), provider_name: "" }] }, { data: [row(A), row(A)] }, { data: [{ ...row(A), capabilities: "run_evaluation" }] },
    { data: [{ ...row(A), capabilities: [1] }] }]) {
    assert.deepEqual(parseWorkspaces(body), bad, JSON.stringify(body));
  }
  // A capability the Lab does not know (a newer server) gates nothing here: it is dropped, not trusted.
  const newer = parseWorkspaces({ data: [{ ...row(B), capabilities: ["read_aggregate_health", "fly"] }] });
  assert.deepEqual(newer, { ok: true, memberships: [B] });
});

test("L1-M04 a membership id is read back at any UUID version, either case (shapes UUID_ANY_RE, LAB-10)", () => {
  const v1 = { ...A, providerId: "6BA7B810-9DAD-11D1-80B4-00C04FD430C8" };
  assert.deepEqual(parseWorkspaces(page(v1)), { ok: true, memberships: [v1] });
});

// LAB-07: one role table, a copy of the frozen contract's (the Lab never imports the App at runtime: L1-B04).
test("L1-A10 holds() grants exactly the contracts/v2 ROLE_CAPABILITIES, for every role and capability", async () => {
  // The App's frozen contract, found through the repo checkout @infrx/shared links into (a mutant copy symlinks node_modules).
  const repo = resolve(realpathSync(new URL("../../../node_modules/@infrx/shared", import.meta.url)), "../..");
  const v2 = await import(pathToFileURL(join(repo, "apps/app/lib/contracts/v2/types.ts")).href);
  assert.deepEqual([...ROLES], [...v2.PROVIDER_ROLES]);
  assert.deepEqual([...CAPABILITIES], [...v2.PROVIDER_CAPABILITIES]);
  for (const role of ROLES) {
    for (const capability of CAPABILITIES) assert.equal(holds(role, capability), v2.ROLE_CAPABILITIES[role].includes(capability), `${role} ${capability}`);
  }
  assert.deepEqual(ROLE_CAPABILITIES, v2.ROLE_CAPABILITIES);
});

test("L1-A11 every family port answers capabilities from that one table, not a table of its own", async () => {
  const ports = await Promise.all(["control", "evaluation", "pipelines", "rollouts"].map((f) => import(`../../../lib/services/${f}/port.ts`)));
  for (const port of ports) assert.equal(port.holds, holds);
});

test("L1-A12 holds() on a workspace answers from the API's capability set for it, not from the role table", () => {
  const promoted: Membership = { ...B, capabilities: ["read_aggregate_health", "run_evaluation"] };
  const narrowed: Membership = { ...A, role: "administrator", capabilities: ["read_aggregate_health"] };
  assert.equal(holds(promoted, "run_evaluation"), true);
  assert.equal(holds(narrowed, "manage_members"), false);
  assert.equal(holds(narrowed, "read_aggregate_health"), true);
  // An actor without the API's set (a fake's) still answers from the contract table.
  assert.equal(holds({ providerId: A.providerId, role: "administrator" }, "manage_members"), true);
});

test("L1-M05 a workspace's feature availability is GET /lab/v1/capabilities for that provider; a failed read is unavailable, never disabled", async () => {
  const features = { control: { state: "configured", reason: null, verified_at: "t" }, traces: { state: "disabled", reason: "flag_off", verified_at: "t" } };
  const w = api(200, { provider_org_id: A.providerId, role: "developer", capabilities: A.capabilities, features });
  assert.deepEqual(await workspaceFeatures(w.api, A), { ok: true, features });
  assert.deepEqual(w.sent.map((s) => [s.method, s.url, s.auth]), [["GET", `https://lab-control.example/lab/v1/capabilities?provider_org_id=${A.providerId}`, "Bearer eyJ.session.sig"]]);
  for (const [status, body] of [[503, {}], [404, { error: { code: "not_found", message: "x", request_id: "r", retryable: false } }], [0, null],
    [200, { features: null }], [200, { features: { control: { state: "on" } } }]] as const) {
    assert.deepEqual(await workspaceFeatures(api(status, body).api, A), { ok: false }, JSON.stringify(body));
  }
});

test("L1-A13 an action lands by the workspace's own API capability set: withheld there, it is refused without a call", async () => {
  let calls = 0;
  const call = async () => {
    calls += 1;
    return { ok: true as const, value: 1 };
  };
  assert.equal(await land("/x", { ...B, capabilities: ["read_aggregate_health", "run_evaluation"] }, "run_evaluation", true, call), "/x");
  assert.equal(await land("/x", { ...A, role: "administrator", capabilities: [] }, "run_evaluation", true, call), "/x?refused=denied");
  assert.equal(calls, 1);
});
