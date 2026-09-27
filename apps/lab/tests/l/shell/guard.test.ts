// L1: guard.ts itself, run for real: Next's own notFound(), a fake request cookie store and a fake
// Supabase session client, wired with module hooks (plain `node --test` has no Next runtime, and
// mock.module needs a flag `pnpm test` does not pass).
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { AUTH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};

const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "developer" };
const B = { provider_org_id: "22222222-2222-4222-8222-222222222222", provider_name: "Beta", role: "viewer" };
type World = { user: string | null; rows: unknown[]; cookies: Record<string, string>; clients: unknown[][]; rpcs: unknown[][] };
const world = (globalThis as unknown as { labGuard: World }).labGuard = { user: null, rows: [], cookies: {}, clients: [], rpcs: [] };

const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    const c = globalThis.labGuard.cookies;
    return { getAll: () => Object.entries(c).map(([name, value]) => ({ name, value })),
             get: (name) => (name in c ? { name, value: c[name] } : undefined),
             set: (name, value) => { c[name] = value; } };
  }`,
  "@supabase/ssr": `export function createServerClient(...args) {
    const w = globalThis.labGuard;
    w.clients.push(args);
    return { auth: { getUser: async () => ({ data: { user: w.user === null ? null : { id: w.user } } }) },
             rpc: async (...call) => { w.rpcs.push(call); return { data: w.rows, error: null }; } };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
const { requireProviderSession, requireProviderWorkspace } = await import("../../../lib/auth/guard.ts");

const ENV = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" };
function request(user: string | null, rows: unknown[], cookies: Record<string, string> = {}, env: object = ENV) {
  Object.assign(world, { user, rows, cookies, clients: [], rpcs: [] });
  delete process.env.NEXT_PUBLIC_SUPABASE_URL;
  delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  Object.assign(process.env, env);
}
const is404 = (error: unknown) => (error as { digest?: string }).digest === "NEXT_HTTP_ERROR_FALLBACK;404";
const NOT_READY: [string, () => void][] = [
  ["denied (consumer-only)", () => request("u1", [])],
  ["signed-out", () => request(null, [A])],
  ["unavailable (misconfigured)", () => request("u1", [A], {}, {})],
];

test("L1-G01 a page gets a workspace only when one is selected: the picker state is a 404, never the first workspace", async () => {
  request("u1", [A]);
  assert.equal((await requireProviderWorkspace()).providerName, "Acme");
  request("u1", [A, B], { [WORKSPACE_COOKIE]: B.provider_org_id });
  assert.equal((await requireProviderWorkspace()).providerName, "Beta");
  request("u1", [A, B]);
  await assert.rejects(requireProviderWorkspace(), is404);
  for (const [, arrange] of NOT_READY) {
    arrange();
    await assert.rejects(requireProviderWorkspace(), is404);
  }
});

test("L1-G02 the selection action runs for any provider session and is a 404 for everyone else", async () => {
  request("u1", [A, B]);
  assert.equal((await requireProviderSession()).kind, "select");
  request("u1", [A]);
  assert.equal((await requireProviderSession()).kind, "ready");
  for (const [, arrange] of NOT_READY) {
    arrange();
    await assert.rejects(requireProviderSession(), is404);
  }
});

test("L1-G03 the guard reads through the Lab's own session client and sends no identity", async () => {
  request("u1", [A], { [AUTH_COOKIE]: "t" });
  await requireProviderWorkspace();
  assert.equal(world.clients.length, 1);
  const [url, key, options] = world.clients[0] as [string, string, { cookieOptions?: { name: string } }];
  assert.deepEqual([url, key, options.cookieOptions?.name], [ENV.NEXT_PUBLIC_SUPABASE_URL, ENV.NEXT_PUBLIC_SUPABASE_ANON_KEY, AUTH_COOKIE]);
  assert.deepEqual(world.rpcs, [["lab_provider_memberships"]]);
});
