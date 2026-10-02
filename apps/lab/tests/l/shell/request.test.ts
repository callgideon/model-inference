// L1: one request's access from its cookies and the API (guard.ts passes the real cookie store).
import assert from "node:assert/strict";
import test from "node:test";
import { AUTH_COOKIE, REFRESH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";
import { accessFromRequest } from "../../../lib/auth/request.ts";

const ENV = { NODE_ENV: "development", LAB_API_URL: "https://lab-control.example" };
const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "administrator", capabilities: ["read_aggregate_health"] };
const B = { provider_org_id: "22222222-2222-4222-8222-222222222222", provider_name: "Beta", role: "viewer", capabilities: ["read_aggregate_health"] };

function world(cookies: Record<string, string> = {}, status = 200) {
  const sent: { url: string; auth: string | null; cookie: string | null }[] = [];
  const store = { get: (name: string) => (name in cookies ? { name, value: cookies[name] } : undefined) };
  const fetch = (async (url: string, init: RequestInit) => {
    const h = new Headers(init.headers);
    sent.push({ url: String(url), auth: h.get("authorization"), cookie: h.get("cookie") });
    return new Response(JSON.stringify(status === 200 ? { data: [A, B] } : { error: { code: "unauthenticated", message: "m", request_id: "r", retryable: false } }), { status });
  }) as typeof globalThis.fetch;
  return { store, fetch, sent };
}

test("L1-R01 a misconfigured Lab is unavailable and sends nothing", async () => {
  const w = world({ [AUTH_COOKIE]: "t" });
  assert.deepEqual(await accessFromRequest({ NODE_ENV: "production" }, w.store, w.fetch), { kind: "unavailable" });
  assert.deepEqual(await accessFromRequest({ NODE_ENV: "development" }, w.store, w.fetch), { kind: "unavailable" });
  assert.equal(w.sent.length, 0);
});

test("L1-R02 the read goes to LAB_API_URL as the session cookie's own token, and forwards no cookie", async () => {
  const w = world({ [AUTH_COOKIE]: "eyJ.session.sig", [REFRESH_COOKIE]: "refresh", [WORKSPACE_COOKIE]: A.provider_org_id });
  await accessFromRequest(ENV, w.store, w.fetch);
  assert.deepEqual(w.sent, [{ url: "https://lab-control.example/lab/v1/workspaces", auth: "Bearer eyJ.session.sig", cookie: null }]);
});

test("L1-R03 the user comes from the session and the workspace from the Lab's cookie, re-checked against memberships", async () => {
  const chosen = world({ [AUTH_COOKIE]: "t", [WORKSPACE_COOKIE]: B.provider_org_id });
  const access = await accessFromRequest(ENV, chosen.store, chosen.fetch);
  assert.equal(access.kind === "ready" && access.workspace.providerName, "Beta");
  const none = world({ [AUTH_COOKIE]: "t" });
  assert.equal((await accessFromRequest(ENV, none.store, none.fetch)).kind, "select");
  const out = world({ [WORKSPACE_COOKIE]: B.provider_org_id });
  assert.deepEqual(await accessFromRequest(ENV, out.store, out.fetch), { kind: "signed-out" });
  assert.equal(out.sent.length, 0);
  const dead = world({ [AUTH_COOKIE]: "expired" }, 401);
  assert.deepEqual(await accessFromRequest(ENV, dead.store, dead.fetch), { kind: "signed-out" });
});
