// WR-P4-1 swap: the annotation and training pages' port is the HTTP adapter, configured only by server env
// (LAB_API_URL, unset = unavailable) and carrying the session's own access token, never a service
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
const { pipelinesPort } = await import("../../lib/services/pipelines/port.ts");

const A = "a0000001-0000-4000-8000-000000000001";
const actor = { providerId: A, role: "developer" as const };
const ENV = { LAB_API_URL: "https://api.example" };

function answering(status: number, body: unknown) {
  const sent: { url: string; auth: string | null }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization") });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
  return sent;
}

test("P4-W01 LAB_API_URL set: the port reads the route as the session's own access token", async () => {
  Object.assign(world, { token: "eyJ0.session.sig", clients: [] });
  const sent = answering(200, { data: [] });
  assert.deepEqual(await pipelinesPort(ENV).runs(actor), { ok: true, value: [] });
  assert.deepEqual(sent, [{ url: `https://api.example/lab/v1/pipelines/training-runs?provider_org_id=${A}`, auth: "Bearer eyJ0.session.sig" }]);
  world.token = null;
  assert.deepEqual(await pipelinesPort(ENV).runs(actor), { ok: false, reason: "unavailable" }, "no session token: nothing is sent");
  assert.equal(sent.length, 1);
});

test("P4-W02 a missing LAB_API_URL or Lab config fails closed: every call unavailable, nothing sent", async () => {
  world.token = "eyJ0.session.sig";
  const sent = answering(200, { data: [] });
  for (const env of [{ ...ENV, LAB_API_URL: "" }, { ...ENV, LAB_API_URL: undefined }, { ...ENV, NODE_ENV: "production" }]) {
    const port = pipelinesPort(env);
    for (const call of [port.runs(actor), port.checkpoints(actor), port.submit(actor, "r")]) assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  }
  assert.equal(sent.length, 0);
});
