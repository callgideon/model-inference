// L1: one request's access from its cookies and a session client (guard.ts passes the real ones).
import assert from "node:assert/strict";
import test from "node:test";
import { AUTH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";
import { accessFromRequest, type ClientOptions } from "../../../lib/auth/request.ts";

const ENV = { NODE_ENV: "development", NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" };
const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "administrator" };
const B = { provider_org_id: "22222222-2222-4222-8222-222222222222", provider_name: "Beta", role: "viewer" };

function world(user: string | null, cookies: Record<string, string> = {}, set = (name: string, value: string) => void (cookies[name] = value)) {
  const built: { url: string; key: string; options: ClientOptions }[] = [];
  let rpcs = 0;
  const store = {
    getAll: () => Object.entries(cookies).map(([name, value]) => ({ name, value })),
    get: (name: string) => (name in cookies ? { name, value: cookies[name] } : undefined),
    set,
  };
  const makeClient = (url: string, key: string, options: ClientOptions) => {
    built.push({ url, key, options });
    return {
      auth: { getUser: async () => ({ data: { user: user === null ? null : { id: user } } }) },
      rpc: async () => {
        rpcs += 1;
        return { data: [A, B], error: null };
      },
    };
  };
  return { store, makeClient, built, cookies, rpcs: () => rpcs };
}

test("L1-R01 a misconfigured Lab is unavailable and builds no client", async () => {
  const w = world("u1");
  assert.deepEqual(await accessFromRequest({ NODE_ENV: "production" }, w.store, w.makeClient), { kind: "unavailable" });
  assert.equal(w.built.length, 0);
});

test("L1-R02 the session client is the Lab's: its cookie options, its env, and the request's cookie store", async () => {
  const w = world("u1", { [AUTH_COOKIE]: "t" });
  await accessFromRequest(ENV, w.store, w.makeClient);
  assert.equal(w.built.length, 1);
  const { url, key, options } = w.built[0];
  assert.deepEqual([url, key], [ENV.NEXT_PUBLIC_SUPABASE_URL, ENV.NEXT_PUBLIC_SUPABASE_ANON_KEY]);
  assert.equal(options.cookieOptions.name, AUTH_COOKIE);
  assert.deepEqual(options.cookies.getAll(), [{ name: AUTH_COOKIE, value: "t" }]);
  options.cookies.setAll([{ name: AUTH_COOKIE, value: "refreshed" }]);
  assert.equal(w.cookies[AUTH_COOKIE], "refreshed");
  const readOnly = world("u1", {}, () => {
    throw new Error("server components cannot set cookies");
  });
  await accessFromRequest(ENV, readOnly.store, readOnly.makeClient);
  assert.doesNotThrow(() => readOnly.built[0].options.cookies.setAll([{ name: AUTH_COOKIE, value: "x" }]));
});

test("L1-R03 the user comes from the session and the workspace from the Lab's cookie, re-checked against memberships", async () => {
  const chosen = world("u1", { [WORKSPACE_COOKIE]: B.provider_org_id });
  const access = await accessFromRequest(ENV, chosen.store, chosen.makeClient);
  assert.equal(access.kind === "ready" && access.workspace.providerName, "Beta");
  const none = world("u1");
  assert.equal((await accessFromRequest(ENV, none.store, none.makeClient)).kind, "select");
  const out = world(null, { [WORKSPACE_COOKIE]: B.provider_org_id });
  assert.deepEqual(await accessFromRequest(ENV, out.store, out.makeClient), { kind: "signed-out" });
  assert.equal(out.rpcs(), 0);
});
