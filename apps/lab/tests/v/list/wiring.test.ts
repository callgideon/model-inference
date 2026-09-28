// V1M / WR-V2-1: the request pages' trace port is the Lab adapter, configured only by server env
// (LAB_TRACES_API_URL, unset = off) and carrying the session's own access token. Next's request APIs
// are faked with module hooks, as in tests/l/shell/guard.test.ts (plain node --test has no Next runtime).
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { AUTH_COOKIE } from "../../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
type World = { token: string | null; cookies: Record<string, string>; clients: unknown[][] };
const world: World = ((globalThis as unknown as { labTraces: World }).labTraces = { token: null, cookies: {}, clients: [] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    const c = globalThis.labTraces.cookies;
    return { getAll: () => Object.entries(c).map(([name, value]) => ({ name, value })) };
  }`,
  "@supabase/ssr": `export function createServerClient(...args) {
    const w = globalThis.labTraces;
    w.clients.push(args);
    return { auth: { getSession: async () => ({ data: { session: w.token === null ? null : { access_token: w.token } } }) } };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier, context);
  },
});
const { tracePorts } = await import("../../../components/traces/detail/port.ts");
const { labTraces } = await import("../../../lib/services/traces/server.ts");

const A = "a0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const actor = { providerId: A, role: "developer" as const };
const ENV = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_TRACES_API_URL: "https://api.example" };

function answering(status: number, body: unknown) {
  const sent: { url: string; auth: string | null }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization") });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
  return sent;
}

test("V1M-W01 the detail page's trace port is the Lab adapter, reading with the session's access token", async () => {
  const saved = { ...process.env };
  Object.assign(process.env, ENV);
  Object.assign(world, { token: "eyJ0.session.sig", cookies: { [AUTH_COOKIE]: "c" }, clients: [] });
  try {
    const sent = answering(404, { refusal: "not_found" });
    assert.deepEqual(await tracePorts().traces.detail(actor, REQ), { ok: false, reason: "not_found" });
    assert.deepEqual(sent, [{ url: `https://api.example/lab/v1/traces/${REQ}?provider_org_id=${A}`, auth: "Bearer eyJ0.session.sig" }]);
    const [url, key, options] = world.clients[0] as [string, string, { cookieOptions: { name: string }; cookies: { getAll(): unknown[] } }];
    assert.deepEqual([url, key, options.cookieOptions?.name, options.cookies.getAll()], [ENV.NEXT_PUBLIC_SUPABASE_URL, "anon", AUTH_COOKIE, [{ name: AUTH_COOKIE, value: "c" }]]);
    world.token = null;
    assert.deepEqual(await tracePorts().traces.detail(actor, REQ), { ok: false, reason: "unavailable" }, "no session token: nothing is sent");
    assert.equal(sent.length, 1);
  } finally {
    process.env = saved;
  }
});

test("V1M-W02 unset LAB_TRACES_API_URL or Supabase config is off: every read unavailable, nothing sent", async () => {
  world.token = "eyJ0.session.sig";
  const sent = answering(200, { data: [], next_cursor: null });
  for (const env of [{ ...ENV, LAB_TRACES_API_URL: "" }, { ...ENV, NEXT_PUBLIC_SUPABASE_ANON_KEY: "" }]) {
    const port = labTraces(env);
    assert.deepEqual([await port.list(actor, null), await port.detail(actor, REQ)], [{ ok: false, reason: "unavailable" }, { ok: false, reason: "unavailable" }]);
  }
  assert.equal(sent.length, 0);
  assert.deepEqual(await labTraces(ENV).list(actor, null), { ok: true, value: { items: [], next_cursor: null } });
});
