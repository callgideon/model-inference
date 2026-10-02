// LAB-05: the datasets pages' port is the HTTP adapter only with a configured backend URL and a Lab config,
// and it carries the session's own access token (lib/auth/session.ts), never a service key. Next's request
// APIs are faked with module hooks, as in tests/b/wiring.test.ts.
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
  // AP-09: the session token is the Lab session cookie itself (lib/auth/session.ts).
  "next/headers": `export async function cookies() {
    const t = globalThis.labSession.token;
    return { get: (name) => (name === "${AUTH_COOKIE}" && t !== null ? { name, value: t } : undefined) };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier, context);
  },
});
const { datasetsPort } = await import("../../lib/services/datasets/server.ts");

const A = "a0000001-0000-4000-8000-000000000001";
const ENV = { LAB_API_URL: "https://api.example" };

function answering(status: number, body: unknown) {
  const sent: { url: string; auth: string | null }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization") });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
  return sent;
}

async function withEnv<T>(env: Record<string, string | undefined>, run: () => Promise<T>): Promise<T> {
  const saved = { ...process.env };
  for (const [k, v] of Object.entries(env)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  try {
    return await run();
  } finally {
    process.env = saved;
  }
}

test("N4-W01 the datasets port reads its backend as the session's own access token; no token, no backend URL or no Lab config sends nothing", async () => {
  Object.assign(world, { token: "eyJ0.session.sig", clients: [] });
  const sent = answering(403, { error: "denied" });
  const denied = await withEnv(ENV, async () => (await datasetsPort()).versions(A));
  assert.equal(denied.ok, false);
  assert.deepEqual(sent, [{ url: `https://api.example/lab/v1/providers/${A}/datasets/versions`, auth: "Bearer eyJ0.session.sig" }]);
  world.token = null;
  assert.deepEqual(await withEnv(ENV, async () => (await datasetsPort()).versions(A)), { ok: false, error: "unavailable", detail: "the session has no token" });
  world.token = "eyJ0.session.sig";
  for (const env of [{ ...ENV, LAB_API_URL: undefined }, { ...ENV, NODE_ENV: "production" }]) {
    assert.deepEqual(await withEnv(env, async () => (await datasetsPort()).versions(A)), { ok: false, error: "unavailable", detail: "the datasets service is not configured" });
  }
  assert.equal(sent.length, 1);
});
