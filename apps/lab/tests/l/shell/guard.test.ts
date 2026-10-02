// L1: guard.ts itself, run for real: Next's own notFound(), a fake request cookie store (module hooks:
// plain `node --test` has no Next runtime, and mock.module needs a flag `pnpm test` does not pass) and
// a fake API behind `fetch` (AP-09: the memberships are GET /lab/v1/workspaces).
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { AUTH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};

const A = { provider_org_id: "11111111-1111-4111-8111-111111111111", provider_name: "Acme", role: "developer", capabilities: ["run_evaluation"] };
const B = { provider_org_id: "22222222-2222-4222-8222-222222222222", provider_name: "Beta", role: "viewer", capabilities: [] };
type World = { cookies: Record<string, string>; rows: unknown[]; sent: { url: string; auth: string | null; body: unknown }[] };
const world: World = ((globalThis as unknown as { labGuard: World }).labGuard = { cookies: {}, rows: [], sent: [] });
globalThis.fetch = (async (url: string, init: RequestInit) => {
  world.sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization"), body: init.body ?? null });
  return new Response(JSON.stringify({ data: world.rows }), { status: 200 });
}) as typeof fetch;

const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    const c = globalThis.labGuard.cookies;
    return { getAll: () => Object.entries(c).map(([name, value]) => ({ name, value })),
             get: (name) => (name in c ? { name, value: c[name] } : undefined),
             set: (name, value) => { c[name] = value; } };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
const { requireProviderSession, requireProviderWorkspace } = await import("../../../lib/auth/guard.ts");

const ENV = { LAB_API_URL: "https://lab-control.example" };
/** `user` null = no session cookie; otherwise the user's access token rides in it. */
function request(user: string | null, rows: unknown[], cookies: Record<string, string> = {}, env: object = ENV) {
  Object.assign(world, { rows, cookies: user === null ? cookies : { [AUTH_COOKIE]: `token-${user}`, ...cookies }, sent: [] });
  delete process.env.LAB_API_URL;
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

test("L1-G03 the guard reads memberships from the API as the session's own token and sends no identity", async () => {
  request("u1", [A]);
  await requireProviderWorkspace();
  assert.deepEqual(world.sent, [{ url: "https://lab-control.example/lab/v1/workspaces", auth: "Bearer token-u1", body: null }]);
});
