// C3L JUDGE-BUDGET + LAB-ACCESS: the Lab's judge actions. The fake RPC stands for lab-sql's
// SECURITY DEFINER functions (SR-C3L-1): it refuses a foreign grantor/model and an expired grant
// with 42501, answers one run per run_id, and records every call so a test can see what was sent.
import assert from "node:assert/strict";
import test from "node:test";
import type { Membership } from "../../../lib/auth/access.ts";
import { AUTH_COOKIE } from "../../../lib/auth/config.ts";
import { PAGE_MAX, RPC, configure, listCalibration, requestRun, sessionRpc, setBudget, type Rpc } from "../../../lib/services/judge/core.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const B = "b0000000-0000-4000-8000-00000000000b";
const GRANTOR = "c1000000-0000-4000-8000-0000000000c1";
const FOREIGN = "c2000000-0000-4000-8000-0000000000c2";
const MODEL = "d0000009-0000-4000-8000-000000000009";
const RUN = "e0000000-0000-4000-8000-00000000000e";
const CONFIG = "f0000000-0000-4000-8000-00000000000f";
const payer = (provider: string) => `lab:payer:${provider}:${"b".repeat(8)}-0000-4000-8000-${"b".repeat(12)}@sha256:${"c".repeat(64)}`;
const dev: Membership = { providerId: A, providerName: "Provider A", role: "developer" };
const admin: Membership = { ...dev, role: "administrator" };
const viewer: Membership = { ...dev, role: "viewer" };

type Call = { name: string; args: Record<string, unknown> };
function server(opts: { expired?: boolean; rows?: number; fail?: "throw" | "error" } = {}) {
  const calls: Call[] = [];
  const runs = new Map<unknown, { run_id: unknown; seq: number }>();
  const rpc: Rpc = async (name, args) => {
    calls.push({ name, args });
    await new Promise((r) => setTimeout(r, 1));
    if (opts.fail === "throw") throw new Error("transport");
    if (opts.fail === "error") return { data: null, error: { code: "08006" } };
    if (args.p_grantor_org_id === FOREIGN || opts.expired) return { data: null, error: { code: "42501" } };
    if (name === RPC.request) {
      if (!runs.has(args.p_run_id)) runs.set(args.p_run_id, { run_id: args.p_run_id, seq: runs.size + 1 });
      return { data: runs.get(args.p_run_id), error: null };
    }
    if (name === RPC.calibration) {
      const n = opts.rows ?? (args.p_limit as number);
      return { data: Array.from({ length: n }, (_, i) => ({ label_id: `label-${i}` })), error: null };
    }
    return { data: { ok: true }, error: null };
  };
  return { rpc, calls, runs };
}

const config = (extra: Record<string, unknown> = {}) => ({
  grantor_org_id: GRANTOR, model_id: MODEL, judge_model: "claude-opus-5", rubric_version: "1", sample_size: "50", ...extra,
});

test("C3L-A01 the provider comes from the guarded workspace, never from the form", async () => {
  const s = server();
  const forged = { provider_org_id: B, p_provider_org_id: B };
  assert.equal((await configure(s.rpc, dev, config(forged))).ok, true);
  assert.equal((await requestRun(s.rpc, dev, { run_id: RUN, config_id: CONFIG, payer_ref: payer(A), ...forged })).ok, true);
  assert.equal((await listCalibration(s.rpc, dev, forged)).ok, true);
  assert.equal((await setBudget(s.rpc, admin, { payer_ref: payer(A), limit_usd: "10.00000000", ...forged })).ok, true);
  assert.equal(s.calls.length, 4);
  for (const call of s.calls) assert.equal(call.args.p_provider_org_id, A);
  for (const call of s.calls) assert.equal(Object.keys(call.args).some((k) => /user|uid|role/.test(k)), false);
});

test("C3L-A02 malformed org, model or run ids are refused before any call", async () => {
  const s = server();
  for (const bad of [{ grantor_org_id: "not-a-uuid" }, { model_id: `${MODEL}' or 1=1` }, { grantor_org_id: GRANTOR.toUpperCase() },
    { judge_model: "../etc" }, { rubric_version: "0" }, { sample_size: "201" }, { sample_size: "1e2" }]) {
    assert.deepEqual(await configure(s.rpc, dev, config(bad)), { ok: false, reason: "invalid" });
  }
  assert.deepEqual(await requestRun(s.rpc, dev, { run_id: "x", config_id: CONFIG, payer_ref: payer(A) }), { ok: false, reason: "invalid" });
  assert.deepEqual(await requestRun(s.rpc, dev, { run_id: RUN, config_id: "nope", payer_ref: payer(A) }), { ok: false, reason: "invalid" });
  assert.equal(s.calls.length, 0);
});

test("C3L-A03 a well-formed foreign org or model is the server's refusal: denied, nothing else", async () => {
  const s = server();
  assert.deepEqual(await configure(s.rpc, dev, config({ grantor_org_id: FOREIGN })), { ok: false, reason: "denied" });
  assert.equal(s.calls.length, 1);
});

test("C3L-A04 an expired grant denies the configuration, the run and the calibration page", async () => {
  const s = server({ expired: true });
  assert.deepEqual(await configure(s.rpc, dev, config()), { ok: false, reason: "denied" });
  assert.deepEqual(await requestRun(s.rpc, dev, { run_id: RUN, config_id: CONFIG, payer_ref: payer(A) }), { ok: false, reason: "denied" });
  assert.deepEqual(await listCalibration(s.rpc, dev, {}), { ok: false, reason: "denied" });
});

test("C3L-A05 a failed or thrown call is unavailable, never denied and never data", async () => {
  for (const fail of ["throw", "error"] as const) {
    const s = server({ fail });
    assert.deepEqual(await configure(s.rpc, dev, config()), { ok: false, reason: "unavailable" });
    assert.deepEqual(await listCalibration(s.rpc, dev, {}), { ok: false, reason: "unavailable" });
  }
});

test("C3L-A06 a viewer has no judge action; only an administrator sets a budget", async () => {
  const s = server();
  assert.deepEqual(await configure(s.rpc, viewer, config()), { ok: false, reason: "denied" });
  assert.deepEqual(await requestRun(s.rpc, viewer, { run_id: RUN, config_id: CONFIG, payer_ref: payer(A) }), { ok: false, reason: "denied" });
  assert.deepEqual(await listCalibration(s.rpc, viewer, {}), { ok: false, reason: "denied" });
  assert.deepEqual(await setBudget(s.rpc, dev, { payer_ref: payer(A), limit_usd: "10.00000000" }), { ok: false, reason: "denied" });
  assert.equal(s.calls.length, 0);
});

test("C3L-B01 a budget is PROVIDER_USD, exact to 1e-8, for this provider's own named payer", async () => {
  const s = server();
  for (const bad of [{ payer_ref: payer(B) }, { payer_ref: payer(A).replace("lab:payer:", "lab:grant:") }, { payer_ref: undefined },
    { limit_usd: "10" }, { limit_usd: "-1.00000000" }, { limit_usd: "1.000000001" }, { limit_usd: "1e3" }]) {
    assert.deepEqual(await setBudget(s.rpc, admin, { payer_ref: payer(A), limit_usd: "10.00000000", ...bad }), { ok: false, reason: "invalid" });
  }
  assert.deepEqual(await requestRun(s.rpc, dev, { run_id: RUN, config_id: CONFIG, payer_ref: payer(B) }), { ok: false, reason: "invalid" });
  assert.equal(s.calls.length, 0);
  await setBudget(s.rpc, admin, { payer_ref: payer(A), limit_usd: "10.00000000" });
  assert.deepEqual(s.calls[0].args.p_limit, { unit: "PROVIDER_USD", value: "10.00000000" });
  assert.equal(s.calls[0].args.p_payer_ref, payer(A));
});

test("C3L-B02 a double-click submit is one run: both clicks carry the form's run id", async () => {
  const s = server();
  const form = { run_id: RUN, config_id: CONFIG, payer_ref: payer(A) };
  const [first, second] = await Promise.all([requestRun(s.rpc, dev, form), requestRun(s.rpc, dev, form)]);
  assert.deepEqual(first, second);
  assert.equal(s.runs.size, 1);
  assert.deepEqual(s.calls.map((c) => c.args.p_run_id), [RUN, RUN]);
});

test("C3L-P01 calibration pages are bounded: the limit is clamped, a bad cursor or limit is refused", async () => {
  const s = server();
  await listCalibration(s.rpc, dev, { limit: "500" });
  await listCalibration(s.rpc, dev, {});
  assert.deepEqual(s.calls.map((c) => c.args.p_limit), [PAGE_MAX, PAGE_MAX]);
  for (const bad of [{ limit: "0" }, { limit: "-1" }, { limit: "abc" }, { after: "label-3" }]) {
    assert.deepEqual(await listCalibration(s.rpc, dev, bad), { ok: false, reason: "invalid" });
  }
  assert.equal(s.calls.length, 2);
});

test("C3L-P02 a full page carries the next cursor, a short page ends, an over-long page is refused", async () => {
  const full = await listCalibration(server().rpc, dev, { limit: "3", after: CONFIG });
  assert.deepEqual(full.ok && (full.data as { next: unknown }).next, "label-2");
  const short = await listCalibration(server({ rows: 2 }).rpc, dev, { limit: "3" });
  assert.deepEqual(short.ok && (short.data as { next: unknown }).next, null);
  assert.deepEqual(await listCalibration(server({ rows: PAGE_MAX + 1 }).rpc, dev, {}), { ok: false, reason: "unavailable" });
});

test("C3L-S01 the calls ride the user's own Lab session; a misconfigured Lab builds no client", async () => {
  const env = { NEXT_PUBLIC_SUPABASE_URL: "https://sb.example", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" };
  const made: unknown[][] = [];
  const sent: unknown[][] = [];
  const set: unknown[][] = [];
  const store = { getAll: () => [{ name: AUTH_COOKIE, value: "s" }], set: (...a: unknown[]) => set.push(a) };
  const rpc = sessionRpc(env, store, (url, key, options) => {
    made.push([url, key, options]);
    options.cookies.setAll([{ name: AUTH_COOKIE, value: "t", options: { path: "/" } }]);
    return { rpc: async (name, args) => (sent.push([name, args]), { data: [], error: null }) };
  });
  await rpc(RPC.calibration, { p_limit: 1 });
  const [[url, key, options]] = made as [[string, string, { cookieOptions: unknown; cookies: { getAll(): unknown } }]];
  assert.deepEqual([url, key, options.cookieOptions], ["https://sb.example", "anon", { name: AUTH_COOKIE, path: "/", sameSite: "lax", secure: false }]);
  assert.deepEqual(options.cookies.getAll(), [{ name: AUTH_COOKIE, value: "s" }]);
  assert.deepEqual(set, [[AUTH_COOKIE, "t", { path: "/" }]]);
  assert.deepEqual(sent, [[RPC.calibration, { p_limit: 1 }]]);
  const none: unknown[] = [];
  const off = sessionRpc({}, store, () => (none.push(1), { rpc: async () => ({ data: [], error: null }) }));
  assert.equal(none.length, 0);
  assert.deepEqual(await listCalibration(off, dev, {}), { ok: false, reason: "unavailable" });
});
