// J3 / WR-V3-1 JUDGE-SCORES: the per-request judge read behind V3's JudgePort. The fake RPC stands for
// lab-sql's `lab_judge_runs(p_provider_org_id, p_request_id)` (SECURITY DEFINER, developer+ of the
// provider, that provider's runs only); the adapter never trusts a row it cannot fully read.
import assert from "node:assert/strict";
import test from "node:test";
import type { JudgeRun } from "../../../components/traces/judge/port.ts";
import { RUNS_RPC, rpcJudgePort } from "../../../lib/services/judge/runs.ts";
import type { Rpc } from "../../../lib/services/judge/core.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const REQ = "c0000000-0000-4000-8000-0000000000c1";
const dev = { providerId: A, role: "developer" as const };

const run = (over: Partial<JudgeRun> = {}): JudgeRun => ({
  runId: "e0000000-0000-4000-8000-00000000000e", mode: "live", state: "collected", judgeModel: "claude-opus-5",
  rubricVersion: 1, media: true, reservedUsd: "0.12000000", actualUsd: "0.01000000",
  scores: [{ criterion: "groundedness", score: 4, max: 5, requiresMedia: true }], overallPass: true,
  calibration: { state: "calibrated", labels: 30, required: 30, agreement: 0.8, interval: [0.61, 0.99] },
  ...over,
});

function server(answer: { data?: unknown; error?: unknown; throws?: boolean }) {
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const rpc: Rpc = async (name, args) => {
    calls.push({ name, args });
    if (answer.throws) throw new Error("transport");
    return { data: answer.data ?? null, error: answer.error ?? null };
  };
  return { port: rpcJudgePort(async () => rpc), calls };
}

test("J3L-R01 the read names the session's provider and the request, and returns the rows", async () => {
  const s = server({ data: [run()] });
  const out = await s.port.runs(dev, REQ);
  assert.deepEqual(out, { ok: true, value: [run()] });
  assert.deepEqual(s.calls, [{ name: RUNS_RPC, args: { p_provider_org_id: A, p_request_id: REQ } }]);
});

test("J3L-R02 a viewer or a malformed request id makes no call", async () => {
  const s = server({ data: [run()] });
  assert.deepEqual(await s.port.runs({ ...dev, role: "viewer" }, REQ), { ok: false, reason: "denied" });
  assert.deepEqual(await s.port.runs(dev, "not-a-uuid"), { ok: false, reason: "unavailable" });
  assert.deepEqual(await s.port.runs(dev, REQ.toUpperCase()), { ok: false, reason: "unavailable" });
  assert.equal(s.calls.length, 0);
});

test("J3L-R03 the server's refusal is denied; any other failure is unavailable", async () => {
  for (const code of ["42501", "P0002"]) {
    assert.deepEqual(await server({ error: { code } }).port.runs(dev, REQ), { ok: false, reason: "denied" });
  }
  assert.deepEqual(await server({ error: { code: "08006" } }).port.runs(dev, REQ), { ok: false, reason: "unavailable" });
  assert.deepEqual(await server({ throws: true }).port.runs(dev, REQ), { ok: false, reason: "unavailable" });
  assert.deepEqual(await server({ data: { rows: [] } }).port.runs(dev, REQ), { ok: false, reason: "unavailable" });
});

test("J3L-R04 one malformed row fails the whole read closed", async () => {
  const bad: unknown[] = [
    { ...run(), mode: "sandbox" },
    { ...run(), state: "settled" },
    { ...run(), reservedUsd: "0.12" },
    { ...run(), actualUsd: 0.01 },
    { ...run(), rubricVersion: 1.5 },
    { ...run(), media: "yes" },
    { ...run(), scores: [{ criterion: "x", score: "4", max: 5, requiresMedia: false }] },
    { ...run(), overallPass: "true" },
    { ...run(), calibration: { ...run().calibration, state: "insufficient", labels: -1 } },
    { ...run(), calibration: { ...run().calibration, interval: [0.9, 0.1] } },
    null,
  ];
  for (const row of bad) {
    assert.deepEqual(await server({ data: [run(), row] }).port.runs(dev, REQ), { ok: false, reason: "unavailable" }, JSON.stringify(row));
  }
});

test("J3L-R05 a no-media pass or an unsupported 'calibrated' is never passed through", async () => {
  const blind = run({ media: false, overallPass: true });
  assert.deepEqual(await server({ data: [blind] }).port.runs(dev, REQ), { ok: false, reason: "unavailable" });
  const textOnly = run({ media: false, scores: [{ criterion: "format", score: 4, max: 5, requiresMedia: false }] });
  assert.equal((await server({ data: [textOnly] }).port.runs(dev, REQ)).ok, true);
  const claims = [
    { state: "calibrated", labels: 12, required: 30, agreement: 0.9, interval: [0.7, 1] },
    { state: "calibrated", labels: 30, required: 30, agreement: null, interval: [0.7, 1] },
    { state: "calibrated", labels: 30, required: 30, agreement: 0.9, interval: null },
  ] as const;
  for (const calibration of claims) {
    const row = run({ calibration: { ...calibration, interval: calibration.interval ? [...calibration.interval] : null } });
    assert.deepEqual(await server({ data: [row] }).port.runs(dev, REQ), { ok: false, reason: "unavailable" });
  }
});
