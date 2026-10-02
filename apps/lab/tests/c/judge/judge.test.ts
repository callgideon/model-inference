// C3L JUDGE-BUDGET + LAB-ACCESS, AP-09 09c: the Lab's judge actions over AP-08's `/lab/v1/judge/*`
// (replaces the lab_judge_* RPCs). The fake API stands for the routes: it refuses a foreign grantor
// with 403, answers one run per Idempotency-Key, and records every request so a test can see what was
// sent. The routes re-check everything; these pure actions refuse a lower role and malformed input
// before any call, take the provider only from the guarded workspace, and fail closed.
import assert from "node:assert/strict";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import type { Membership } from "../../../lib/auth/access.ts";
import { calibration, configure, requestRun, setBudget } from "../../../lib/services/judge/core.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const B = "b0000000-0000-4000-8000-00000000000b";
const GRANTOR = "c1000000-0000-4000-8000-0000000000c1";
const FOREIGN = "c2000000-0000-4000-8000-0000000000c2";
const MODEL = "d0000009-0000-4000-8000-000000000009";
const RUN = "e0000000-0000-4000-8000-00000000000e";
const CONFIG = "f0000000-0000-4000-8000-00000000000f";
const KEY = "a1000000-0000-4000-8000-0000000000a1";
const payer = (provider: string) => `lab:payer:${provider}:${"b".repeat(8)}-0000-4000-8000-${"b".repeat(12)}@sha256:${"c".repeat(64)}`;
const dev: Membership = { providerId: A, providerName: "Provider A", role: "developer", capabilities: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"] };
const admin: Membership = { ...dev, role: "administrator", capabilities: [...dev.capabilities, "propose_publication", "manage_members"] };
const viewer: Membership = { ...dev, role: "viewer", capabilities: ["read_aggregate_health"] };
const ERR = (code: string) => ({ error: { code, message: "m", request_id: "q", retryable: false } });

type Sent = { method: string; path: string; query: Record<string, string>; key: string | null; auth: string | null; body: unknown };
function server(opts: { status?: number; body?: unknown; throws?: boolean } = {}) {
  const sent: Sent[] = [];
  const runs = new Map<string, number>();
  const fetch = (async (url: string, init: RequestInit) => {
    const u = new URL(url);
    const h = new Headers(init.headers);
    const body = init.body ? JSON.parse(String(init.body)) : null;
    sent.push({ method: String(init.method), path: u.pathname, query: Object.fromEntries(u.searchParams), key: h.get("idempotency-key"), auth: h.get("authorization"), body });
    if (opts.throws) throw new Error("transport");
    if (opts.status) return new Response(JSON.stringify(opts.body ?? ERR("x")), { status: opts.status });
    if (body?.grantor_org_id === FOREIGN) return new Response(JSON.stringify(ERR("forbidden")), { status: 403 });
    if (u.pathname.endsWith("/runs")) {
      if (!runs.has(h.get("idempotency-key")!)) runs.set(h.get("idempotency-key")!, runs.size + 1);
      return new Response(JSON.stringify({ operation_id: `op-${runs.get(h.get("idempotency-key")!)}`, state: "queued" }), { status: 202 });
    }
    if (u.pathname.endsWith("/calibration")) return Response.json({ config_id: u.searchParams.get("config_id"), state: "insufficient", labels: 3, required: 30, agreement: null, interval: null });
    return Response.json({ ok: true }, { status: init.method === "POST" ? 201 : 200 });
  }) as typeof globalThis.fetch;
  return { api: labApi({ baseUrl: "https://lab-control.example", fetch, session: () => ({ token: "tok" }) }), sent, runs };
}

const config = (extra: Record<string, unknown> = {}) => ({
  grantor_org_id: GRANTOR, model_id: MODEL, judge_model: "claude-opus-5", rubric_version: "1", sample_size: "50", idempotency_key: KEY, ...extra,
});
const run = (extra: Record<string, unknown> = {}) => ({ run_id: RUN, config_id: CONFIG, payer_ref: payer(A), ...extra });

test("C3L-A01 the provider comes from the guarded workspace, never from the form; the call rides the session", async () => {
  const s = server();
  const forged = { provider_org_id: B, p_provider_org_id: B };
  assert.equal((await configure(s.api, dev, config(forged))).ok, true);
  assert.equal((await requestRun(s.api, dev, run(forged))).ok, true);
  assert.equal((await calibration(s.api, dev, { config_id: CONFIG, ...forged })).ok, true);
  assert.equal((await setBudget(s.api, admin, { payer_ref: payer(A), limit_usd: "10.00000000", idempotency_key: KEY, ...forged })).ok, true);
  assert.equal(s.sent.length, 4);
  for (const call of s.sent) {
    assert.deepEqual(Object.keys(call.query).filter((k) => k !== "config_id"), ["provider_org_id"]);
    assert.equal(call.query.provider_org_id, A);
    assert.equal(call.auth, "Bearer tok");
    assert.equal(JSON.stringify(call.body ?? {}).match(/provider|user|role/), null);
  }
});

test("C3L-A02 malformed org, model, run or key ids are refused before any call", async () => {
  const s = server();
  for (const bad of [{ grantor_org_id: "not-a-uuid" }, { model_id: `${MODEL}' or 1=1` }, { grantor_org_id: GRANTOR.toUpperCase() },
    { judge_model: "../etc" }, { rubric_version: "0" }, { rubric_version: "1001" }, { sample_size: "201" }, { sample_size: "1e2" },
    { idempotency_key: undefined }, { idempotency_key: "x" }]) {
    assert.deepEqual(await configure(s.api, dev, config(bad)), { ok: false, reason: "invalid" }, JSON.stringify(bad));
  }
  for (const bad of [{ run_id: "x" }, { run_id: undefined }, { config_id: "nope" }]) {
    assert.deepEqual(await requestRun(s.api, dev, run(bad)), { ok: false, reason: "invalid" });
  }
  for (const bad of [{}, { config_id: "nope" }]) assert.deepEqual(await calibration(s.api, dev, bad), { ok: false, reason: "invalid" });
  assert.equal(s.sent.length, 0);
});

test("C3L-A03 a well-formed foreign org or model is the server's refusal: denied, nothing else", async () => {
  const s = server();
  assert.deepEqual(await configure(s.api, dev, config({ grantor_org_id: FOREIGN })), { ok: false, reason: "denied" });
  assert.equal(s.sent.length, 1);
});

test("C3L-A04 the route's refusals keep their meaning: 401/403/404 denied, 409 conflict, 422 invalid", async () => {
  for (const [status, reason] of [[401, "denied"], [403, "denied"], [404, "denied"], [409, "conflict"], [422, "invalid"]] as const) {
    const s = server({ status, body: ERR("x") });
    assert.deepEqual(await configure(s.api, dev, config()), { ok: false, reason }, String(status));
    assert.deepEqual(await requestRun(s.api, dev, run()), { ok: false, reason }, String(status));
    assert.deepEqual(await calibration(s.api, dev, { config_id: CONFIG }), { ok: false, reason }, String(status));
  }
  // The Lab's legacy `{refusal}` is the same status.
  assert.deepEqual(await configure(server({ status: 403, body: { refusal: "denied" } }).api, dev, config()), { ok: false, reason: "denied" });
});

test("C3L-A05 a failed or thrown call, or a misconfigured Lab, is unavailable, never denied and never data", async () => {
  for (const s of [server({ throws: true }), server({ status: 503, body: ERR("unavailable") }), server({ status: 500, body: "x" }), server({ status: 403, body: "<html>" })]) {
    assert.deepEqual(await configure(s.api, dev, config()), { ok: false, reason: "unavailable" });
    assert.deepEqual(await calibration(s.api, dev, { config_id: CONFIG }), { ok: false, reason: "unavailable" });
  }
  for (const call of [configure(null, dev, config()), requestRun(null, dev, run()), calibration(null, dev, { config_id: CONFIG }),
    setBudget(null, admin, { payer_ref: payer(A), limit_usd: "1.00000000", idempotency_key: KEY })]) {
    assert.deepEqual(await call, { ok: false, reason: "unavailable" });
  }
});

test("C3L-A06 a workspace without run_evaluation has no judge action; only manage_members sets a budget", async () => {
  const s = server();
  assert.deepEqual(await configure(s.api, viewer, config()), { ok: false, reason: "denied" });
  assert.deepEqual(await requestRun(s.api, viewer, run()), { ok: false, reason: "denied" });
  assert.deepEqual(await calibration(s.api, viewer, { config_id: CONFIG }), { ok: false, reason: "denied" });
  assert.deepEqual(await setBudget(s.api, dev, { payer_ref: payer(A), limit_usd: "10.00000000", idempotency_key: KEY }), { ok: false, reason: "denied" });
  // The API's set decides, not the role's name (AP-09: holds() on the workspace).
  assert.deepEqual(await configure(s.api, { ...admin, capabilities: ["read_aggregate_health"] }, config()), { ok: false, reason: "denied" });
  assert.equal(s.sent.length, 0);
});

test("C3L-B01 a budget is PROVIDER_USD, exact to 1e-8, for this provider's own named payer, by PUT with the form's key", async () => {
  const s = server();
  const ok = { payer_ref: payer(A), limit_usd: "10.00000000", idempotency_key: KEY };
  for (const bad of [{ payer_ref: payer(B) }, { payer_ref: payer(A).replace("lab:payer:", "lab:grant:") }, { payer_ref: undefined },
    { limit_usd: "10" }, { limit_usd: "-1.00000000" }, { limit_usd: "1.000000001" }, { limit_usd: "1e3" }, { idempotency_key: undefined }]) {
    assert.deepEqual(await setBudget(s.api, admin, { ...ok, ...bad }), { ok: false, reason: "invalid" }, JSON.stringify(bad));
  }
  assert.deepEqual(await requestRun(s.api, dev, run({ payer_ref: payer(B) })), { ok: false, reason: "invalid" });
  assert.equal(s.sent.length, 0);
  await setBudget(s.api, admin, ok);
  assert.deepEqual(s.sent, [{ method: "PUT", path: `/lab/v1/judge/budgets/${encodeURIComponent(payer(A))}`, query: { provider_org_id: A }, key: KEY, auth: "Bearer tok",
    body: { limit: { amount: "10.00000000", unit: "PROVIDER_USD" } } }]);
});

test("C3L-B02 a double-click submit is one run: both clicks carry the form's run id as the Idempotency-Key", async () => {
  const s = server();
  const [first, second] = await Promise.all([requestRun(s.api, dev, run()), requestRun(s.api, dev, run())]);
  assert.deepEqual(first, second);
  assert.equal(s.runs.size, 1);
  assert.deepEqual(s.sent.map((c) => [c.method, c.path, c.key, c.body]), [
    ["POST", "/lab/v1/judge/runs", RUN, { config_id: CONFIG, payer_ref: payer(A) }],
    ["POST", "/lab/v1/judge/runs", RUN, { config_id: CONFIG, payer_ref: payer(A) }],
  ]);
});

test("C3L-B03 a configuration posts the typed body with numbers and the form's key", async () => {
  const s = server();
  await configure(s.api, dev, config());
  assert.deepEqual(s.sent.map((c) => [c.method, c.path, c.key, c.body]), [["POST", "/lab/v1/judge/configs", KEY,
    { grantor_org_id: GRANTOR, model_id: MODEL, judge_model: "claude-opus-5", rubric_version: 1, sample_size: 50 }]]);
});

test("C3L-P01 calibration is the configuration's agreement as the route reports it, never a page of labels", async () => {
  const s = server();
  const out = await calibration(s.api, dev, { config_id: CONFIG });
  assert.deepEqual(out, { ok: true, data: { config_id: CONFIG, state: "insufficient", labels: 3, required: 30, agreement: null, interval: null } });
  assert.deepEqual(s.sent.map((c) => [c.method, c.path, c.query]), [["GET", "/lab/v1/judge/calibration", { provider_org_id: A, config_id: CONFIG }]]);
});
