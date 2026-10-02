// J3 / WR-V3-1 JUDGE-SCORES, AP-09 09c: the per-request judge read behind V3's JudgePort. Its RPC
// (lab_judge_runs) is gone from the Lab and AP-08's API has no per-request read yet (WR-AP09L-3), so
// the port is honestly unavailable: no database call, no invented runs, and a viewer is still denied.
import assert from "node:assert/strict";
import test from "node:test";
import { judgeRunsPort } from "../../../lib/services/judge/runs.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const REQ = "c0000000-0000-4000-8000-0000000000c1";

test("J3L-R01 until the API serves a request's judge runs, the read is unavailable for a developer and denied for a viewer; nothing is sent", async () => {
  let sent = 0;
  globalThis.fetch = (async () => ((sent += 1), Response.json([]))) as typeof fetch;
  const port = judgeRunsPort();
  assert.deepEqual(await port.runs({ providerId: A, role: "developer" }, REQ), { ok: false, reason: "unavailable" });
  assert.deepEqual(await port.runs({ providerId: A, role: "administrator" }, REQ), { ok: false, reason: "unavailable" });
  assert.deepEqual(await port.runs({ providerId: A, role: "viewer" }, REQ), { ok: false, reason: "denied" });
  assert.equal(sent, 0);
});
