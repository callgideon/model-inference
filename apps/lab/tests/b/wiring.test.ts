// WR-B4-1 swap: the evaluation pages' port is the HTTP adapter, configured only by server env
// (LAB_EVALS_API_URL, unset = unavailable) and carrying the session's own access token, never a service
// key. Next's request APIs are faked with module hooks, as in tests/v/list/wiring.test.ts.
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { AUTH_COOKIE } from "../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
type World = { token: string | null; clients: unknown[][] };
const world: World = ((globalThis as unknown as { labSession: World }).labSession = { token: null, clients: [] });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() { return { getAll: () => [{ name: "${AUTH_COOKIE}", value: "c" }] }; }`,
  "@supabase/ssr": `export function createServerClient(...args) {
    const w = globalThis.labSession;
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
const { evaluationPort } = await import("../../lib/services/evaluation/port.ts");

const A = "a0000001-0000-4000-8000-000000000001";
const actor = { providerId: A, role: "developer" as const };
const ENV = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_EVALS_API_URL: "https://api.example" };

function answering(status: number, body: unknown) {
  const sent: { url: string; auth: string | null }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization") });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
  return sent;
}

test("B4-W01 LAB_EVALS_API_URL set: the port reads the route as the session's own access token", async () => {
  Object.assign(world, { token: "eyJ0.session.sig", clients: [] });
  const sent = answering(200, { data: [] });
  assert.deepEqual(await evaluationPort(ENV).runs(actor), { ok: true, value: [] });
  assert.deepEqual(sent, [{ url: `https://api.example/lab/v1/evaluations/runs?provider_org_id=${A}`, auth: "Bearer eyJ0.session.sig" }]);
  const [url, key, options] = world.clients[0] as [string, string, { cookieOptions: { name: string }; cookies: { getAll(): unknown[] } }];
  assert.deepEqual([url, key, options.cookieOptions?.name, options.cookies.getAll()], [ENV.NEXT_PUBLIC_SUPABASE_URL, "anon", AUTH_COOKIE, [{ name: AUTH_COOKIE, value: "c" }]]);
  world.token = null;
  assert.deepEqual(await evaluationPort(ENV).runs(actor), { ok: false, reason: "unavailable" }, "no session token: nothing is sent");
  assert.equal(sent.length, 1);
});

test("B4-W02 a missing LAB_EVALS_API_URL or Supabase config fails closed: every call unavailable, nothing sent", async () => {
  world.token = "eyJ0.session.sig";
  const sent = answering(200, { data: [] });
  for (const env of [{ ...ENV, LAB_EVALS_API_URL: "" }, { ...ENV, LAB_EVALS_API_URL: undefined }, { ...ENV, NEXT_PUBLIC_SUPABASE_ANON_KEY: "" }]) {
    const port = evaluationPort(env);
    for (const call of [port.runs(actor), port.catalog(actor), port.cancel(actor, "r")]) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  }
  assert.equal(sent.length, 0);
});
