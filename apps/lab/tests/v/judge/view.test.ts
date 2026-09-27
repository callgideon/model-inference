// V3 (JUDGE-SCORES, CONSOLE-FLOWS): judge runs and calibration for one request, derived only from the
// J2/J3 read (WR-V3-1). A dry run is an estimate, a no-media run can never look like groundedness
// success, an ambiguous submit shows its held USD budget, and an uncalibrated judge is never shown as
// calibrated.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeJudge } from "../../../components/traces/judge/fake.ts";
import { judgePort, type Calibration, type JudgeRun } from "../../../components/traces/judge/port.ts";
import { calibrationCopy, JUDGE_COPY, judgeRows } from "../../../components/traces/judge/view.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const none: Calibration = { state: "uncalibrated", labels: 0, required: 30, agreement: null, interval: null };
const run = (over: Partial<JudgeRun>): JudgeRun => ({
  runId: "run-1", mode: "live", state: "collected", judgeModel: "judge-fake-1", rubricVersion: 2, media: true,
  reservedUsd: "0.01200000", actualUsd: "0.00800000",
  scores: [
    { criterion: "relevance", score: 4, max: 5, requiresMedia: false },
    { criterion: "groundedness", score: 5, max: 5, requiresMedia: true },
  ],
  overallPass: true, calibration: none, ...over,
});

test("V3-J01 a dry run is labelled an estimate: no scores, no verdict, nothing reserved", () => {
  const [row] = judgeRows([run({ mode: "dry_run", state: "estimated", actualUsd: null })]);
  assert.equal(row.label, JUDGE_COPY.dry_run);
  assert.deepEqual([row.scores, row.verdict], [[], JUDGE_COPY.no_verdict_dry_run]);
  assert.equal(row.status, "Estimated worst case 0.01200000 USD; nothing reserved, nothing sent.");
});

test("V3-J02 a collected live run shows each score out of its maximum with the judge model and rubric", () => {
  const [row] = judgeRows([run({})]);
  assert.equal(row.label, "Live judge result · judge-fake-1 · rubric v2");
  assert.deepEqual(row.scores, [["relevance", "4/5"], ["groundedness", "5/5"]]);
  assert.deepEqual([row.verdict, row.status], ["pass", "Collected; 0.00800000 USD charged."]);
  assert.equal(judgeRows([run({ overallPass: false })])[0].verdict, "fail");
  assert.equal(judgeRows([run({ overallPass: null })])[0].verdict, "—");
  assert.deepEqual(judgeRows([run({ scores: [{ criterion: "format", score: null, max: 5, requiresMedia: false }] })])[0].scores, [["format", "not scored"]]);
});

test("V3-J03 without media, media criteria read not scored and the verdict is never a pass", () => {
  const [row] = judgeRows([run({ media: false })]);
  assert.deepEqual(row.scores, [["relevance", "4/5"], ["groundedness", JUDGE_COPY.no_media]]);
  assert.equal(row.verdict, JUDGE_COPY.no_media_verdict);
  const textOnly = run({ media: false, scores: [{ criterion: "relevance", score: 4, max: 5, requiresMedia: false }] });
  assert.equal(judgeRows([textOnly])[0].verdict, "pass");
});

test("V3-J04 in-flight and ambiguous runs show the held USD budget and no scores; nothing is ever CREDIT", () => {
  const ambiguous = judgeRows([run({ state: "ambiguous", actualUsd: null })])[0];
  assert.equal(ambiguous.status, "Submit outcome unknown: 0.01200000 USD held until reconciled; it is never resubmitted.");
  assert.deepEqual([ambiguous.scores, ambiguous.verdict], [[], "—"]);
  assert.equal(judgeRows([run({ state: "submitted", actualUsd: null })])[0].status, "Awaiting the judge: 0.01200000 USD held.");
  assert.equal(judgeRows([run({ state: "released", actualUsd: null })])[0].status, "Released; nothing charged.");
  assert.doesNotMatch(JSON.stringify(judgeRows([run({}), run({ state: "ambiguous" }), run({ mode: "dry_run", state: "estimated" })])), /CREDIT/);
});

test("V3-J05 calibration is claimed only with the statistics and enough operator labels behind it", () => {
  const calibrated: Calibration = { state: "calibrated", labels: 42, required: 30, agreement: 0.82, interval: [0.71, 0.9] };
  assert.equal(calibrationCopy(calibrated), "Calibrated against 42 operator labels: agreement 82% (95% CI 71%–90%).");
  assert.equal(calibrationCopy(none), JUDGE_COPY.uncalibrated);
  assert.equal(calibrationCopy({ ...none, state: "insufficient", labels: 3 }), "Not calibrated: 3 of 30 operator labels needed.");
  assert.equal(calibrationCopy({ ...calibrated, labels: 3 }), "Not calibrated: 3 of 30 operator labels needed.");
  for (const bad of [{ agreement: null }, { interval: null }, { state: "insufficient" }] as Partial<Calibration>[])
    assert.equal(calibrationCopy({ ...calibrated, ...bad }), "Not calibrated yet: 42 operator labels, agreement not computed.", JSON.stringify(bad));
  assert.equal(judgeRows([run({})])[0].calibration, JUDGE_COPY.uncalibrated);
});

test("V3-J06 the judge read is the provider's own, developer+, and unavailable until the real adapter is wired", async () => {
  const fake = new FakeJudge();
  fake.add(A, REQ, run({}));
  assert.deepEqual(await fake.runs({ providerId: A, role: "developer" }, REQ), { ok: true, value: [run({})] });
  assert.deepEqual(await fake.runs({ providerId: "b0000001-0000-4000-8000-000000000001", role: "developer" }, REQ), { ok: true, value: [] });
  assert.deepEqual(await fake.runs({ providerId: A, role: "developer" }, "5c000000-0000-4000-8000-0000000000f2"), { ok: true, value: [] });
  assert.deepEqual(await fake.runs({ providerId: A, role: "viewer" }, REQ), { ok: false, reason: "denied" });
  assert.deepEqual(await judgePort().runs({ providerId: A, role: "administrator" }, REQ), { ok: false, reason: "unavailable" });
  assert.ok(JUDGE_COPY.denied && JUDGE_COPY.unavailable && JUDGE_COPY.empty);
});
