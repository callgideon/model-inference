// WR-E3L-J: the control port over the control service's `/lab/v1/control` (R186's factory; the
// gateway's routes/lab_control.py), in the rollouts/http.ts style: the session's own token, the actor's
// provider, the route's snake_case records renamed to the port's keys only, the fixed refusals, and a
// record the pages cannot read failing the whole answer closed. controlPort() is this adapter only when
// LAB_CONTROL_URL is set (and a Lab session can be read); otherwise unavailable, the fake only on its flag.
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { AUTH_COOKIE } from "../../../lib/auth/config.ts";
import { httpControl } from "../../../lib/services/control/http.ts";
import type { Aggregate, Deployment, Model, Proposal } from "../../../lib/services/control/port.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
const session: { token: string | null } = ((globalThis as unknown as { labSession: { token: string | null } }).labSession = { token: "eyJ0.s.t" });
const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() { return { getAll: () => [{ name: "${AUTH_COOKIE}", value: "c" }] }; }`,
  "@supabase/ssr": `export function createServerClient() {
    const t = globalThis.labSession.token;
    return { auth: { getSession: async () => ({ data: { session: t === null ? null : { access_token: t } } }) } };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier, context);
  },
});
const { controlPort } = await import("../../../lib/services/control/port.ts");

const A = { providerId: "a0000000-0000-4000-8000-00000000000a", role: "administrator" as const };
const DIGEST = `sha256:${"a".repeat(64)}`;
const MODEL: Model = { modelId: "nemostation/marlin-2b", revisionLabel: "lab-2", artifactDigest: DIGEST, schemaVersion: "chat.v1", runtime: "vllm@sha256:bb", registeredAt: "2026-09-27T10:00:00Z" };
const DEPLOYMENT: Deployment = {
  deploymentRevisionId: "d1", modelId: "nemostation/marlin-2b", servingVersionId: "s1", revisionLabel: "lab-2", runtime: "vllm@sha256:bb",
  schemaVersion: "chat.v1", rateCardVersion: null, environment: "dev", visibility: "private", state: "active", smoke: "none", createdAt: "2026-09-27T10:00:00Z",
};
const PROPOSAL: Proposal = { proposalId: "p1", kind: "publish", deploymentRevisionId: "d1", state: "proposed", proposedAt: "2026-09-27T10:05:00Z", decidedAt: null };
const AGGREGATE: Aggregate = { deploymentRevisionId: "d1", windowStart: "2026-09-27T09:00:00Z", windowEnd: "2026-09-27T10:00:00Z", requests: 5, errors: 1, p95LatencyMs: null };
const REG = { name: "marlin-2b", artifactDigest: DIGEST, schemaVersion: "chat.v1", runtime: "vllm@sha256:bb" };

type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };
function server(answer: (seen: Seen) => Response, token: () => Promise<string | null> = async () => "eyJ0.a.b") {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const s = { url, method: String(init.method), headers: init.headers as Record<string, string>, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    seen.push(s);
    return answer(s);
  }) as unknown as typeof globalThis.fetch;
  return { seen, port: httpControl({ baseUrl: "https://control.test/", token, fetch }) };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
/** The route's snake_case for a port fixture (the test's own rename, not the adapter's). */
const snake = (v: unknown): unknown => Array.isArray(v) ? v.map(snake)
  : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`), snake(x)])) : v;
const drop = (o: object, key: string) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== key));

test("L4-H01 every call is the session's token and the actor's provider on its route; records come back in the port's keys, values untouched", async () => {
  const lists: Record<string, unknown[]> = { models: [MODEL], deployments: [DEPLOYMENT, { ...DEPLOYMENT, environment: "prod", visibility: "public", rateCardVersion: "rc-1" }], proposals: [PROPOSAL], aggregates: [AGGREGATE] };
  const { seen, port } = server((s) => {
    const path = new URL(s.url).pathname.replace("/lab/v1/control/", "");
    if (s.method === "GET") return json({ data: snake(lists[path]) });
    return path === "proposals" ? json(snake(PROPOSAL), 201) : json(snake({ ...DEPLOYMENT, smoke: path === "register" ? "none" : "passed" }), path === "register" ? 201 : 200);
  });
  assert.deepEqual(await port.models(A), { ok: true, value: lists.models });
  assert.deepEqual(await port.deployments(A), { ok: true, value: lists.deployments });
  assert.deepEqual(await port.proposals(A), { ok: true, value: lists.proposals });
  assert.deepEqual(await port.aggregates(A), { ok: true, value: lists.aggregates });
  assert.deepEqual(await port.register(A, REG), { ok: true, value: DEPLOYMENT });
  assert.deepEqual(await port.smoke(A, "d/1?x"), { ok: true, value: { ...DEPLOYMENT, smoke: "passed" } });
  assert.deepEqual(await port.propose(A, "publish", "d1"), { ok: true, value: PROPOSAL });
  const q = `?provider_org_id=${A.providerId}`;
  const root = "https://control.test/lab/v1/control";
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["GET", `${root}/models${q}`], ["GET", `${root}/deployments${q}`], ["GET", `${root}/proposals${q}`], ["GET", `${root}/aggregates${q}`],
    ["POST", `${root}/register${q}`], ["POST", `${root}/deployments/d%2F1%3Fx/smoke${q}`], ["POST", `${root}/proposals${q}`],
  ]);
  assert.ok(seen.every((s) => s.headers.authorization === "Bearer eyJ0.a.b"));
  assert.deepEqual(seen.map((s) => s.body), [undefined, undefined, undefined, undefined,
    { name: "marlin-2b", artifact_digest: DIGEST, schema_version: "chat.v1", runtime: "vllm@sha256:bb" }, undefined, { kind: "publish", deployment_revision_id: "d1" }]);
  assert.deepEqual(seen.map((s) => s.headers["content-type"]), [undefined, undefined, undefined, undefined, "application/json", undefined, "application/json"]);
});

test("L4-H02 the route's refusals are the port's reasons; anything else, or no answer, is unavailable", async () => {
  const cases: [number, string][] = [[401, "denied"], [403, "denied"], [404, "not_found"], [409, "conflict"], [422, "invalid"], [410, "unavailable"], [503, "unavailable"], [500, "unavailable"]];
  for (const [status, reason] of cases) {
    const { port } = server(() => json({ refusal: "x" }, status));
    for (const answer of [await port.propose(A, "publish", "d1"), await port.deployments(A)]) assert.deepEqual(answer, { ok: false, reason }, String(status));
  }
  const down = server(() => { throw new TypeError("fetch failed"); });
  assert.deepEqual(await down.port.models(A).catch((e: unknown) => ({ threw: String(e) })), { ok: false, reason: "unavailable" });
  const garbled = server(() => new Response("<html>", { status: 200 }));
  assert.deepEqual(await garbled.port.smoke(A, "d1"), { ok: false, reason: "unavailable" });
});

test("L4-H03 one unreadable model, deployment, proposal or aggregate fails the whole answer closed", async () => {
  const answers = (body: unknown) => server(() => json(body)).port;
  const unreadable: [keyof typeof LISTS, unknown][] = [
    ["models", drop(MODEL, "artifactDigest")], ["models", { ...MODEL, registeredAt: null }],
    ["deployments", { ...DEPLOYMENT, environment: "staging" }], ["deployments", { ...DEPLOYMENT, visibility: "unlisted" }], ["deployments", { ...DEPLOYMENT, state: "validating" }],
    ["deployments", { ...DEPLOYMENT, smoke: "skipped" }], ["deployments", drop(DEPLOYMENT, "rateCardVersion")], ["deployments", drop(DEPLOYMENT, "servingVersionId")],
    ["proposals", { ...PROPOSAL, state: "pending" }], ["proposals", { ...PROPOSAL, kind: "delete" }], ["proposals", drop(PROPOSAL, "decidedAt")],
    ["aggregates", { ...AGGREGATE, requests: "5" }], ["aggregates", drop(AGGREGATE, "p95LatencyMs")], ["aggregates", drop(AGGREGATE, "windowEnd")],
  ];
  const LISTS = { models: MODEL, deployments: DEPLOYMENT, proposals: PROPOSAL, aggregates: AGGREGATE };
  for (const [name, bad] of unreadable)
    assert.deepEqual(await answers({ data: snake([LISTS[name], bad]) })[name](A), { ok: false, reason: "unavailable" }, `${name} ${JSON.stringify(bad).slice(0, 80)}`);
  assert.deepEqual(await answers({ data: snake(DEPLOYMENT) }).deployments(A), { ok: false, reason: "unavailable" }, "not a list");
  assert.deepEqual(await answers(snake({ ...DEPLOYMENT, smoke: "maybe" })).register(A, REG), { ok: false, reason: "unavailable" });
  assert.deepEqual(await answers(snake(drop(DEPLOYMENT, "deploymentRevisionId"))).smoke(A, "d1"), { ok: false, reason: "unavailable" });
  assert.deepEqual(await answers(snake(drop(PROPOSAL, "proposalId"))).propose(A, "publish", "d1"), { ok: false, reason: "unavailable" });
});

test("L4-H04 without a session token, or when reading it fails, nothing is sent and every call is unavailable", async () => {
  for (const token of [async () => null, async () => { throw new Error("session store unreadable"); }]) {
    const { seen, port } = server(() => json({ data: [] }), token);
    const calls = [port.models(A), port.deployments(A), port.proposals(A), port.aggregates(A), port.register(A, REG), port.smoke(A, "d1"), port.propose(A, "publish", "d1")];
    for (const call of calls) assert.deepEqual(await call.catch((e: unknown) => ({ threw: String(e) })), { ok: false, reason: "unavailable" });
    assert.equal(seen.length, 0);
  }
});

test("L4-H05 controlPort() is the HTTP adapter only with LAB_CONTROL_URL and a Lab config, carrying the session's own token", async () => {
  const ENV = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon", LAB_CONTROL_URL: "https://control.example" };
  const sent: { url: string; auth: string | null }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    sent.push({ url: String(url), auth: new Headers(init.headers).get("authorization") });
    return json({ data: snake([DEPLOYMENT]) });
  }) as unknown as typeof fetch;
  assert.deepEqual(await controlPort(ENV).deployments(A), { ok: true, value: [DEPLOYMENT] });
  assert.deepEqual(sent, [{ url: `https://control.example/lab/v1/control/deployments?provider_org_id=${A.providerId}`, auth: "Bearer eyJ0.s.t" }]);
  session.token = null;
  assert.deepEqual(await controlPort(ENV).deployments(A), { ok: false, reason: "unavailable" }, "signed out: nothing is sent");
  session.token = "eyJ0.s.t";
  for (const env of [{ ...ENV, LAB_CONTROL_URL: "" }, { ...ENV, NEXT_PUBLIC_SUPABASE_ANON_KEY: undefined }, drop(ENV, "LAB_CONTROL_URL")])
    assert.deepEqual(await controlPort(env).deployments(A), { ok: false, reason: "unavailable" }, JSON.stringify(env));
  assert.equal(sent.length, 1);
  assert.deepEqual(await controlPort({ ...ENV, LAB_CONTROL_PREVIEW: "1" }).deployments(A), { ok: true, value: [] }, "the labelled preview stays on its own flag");
  assert.equal(sent.length, 1);
});
