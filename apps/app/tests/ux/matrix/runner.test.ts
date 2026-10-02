// UX-11 final acceptance: the matrix runner's real-gate parts and NOT RUN causes (run-matrix.mjs), and
// the final matrix it reads. Failure oracle: a real gate that was not run, or could not be, must never
// read as PASS or FAIL - it is BLOCKED naming what it needs; a case a suite reports NOT RUN keeps its own
// reason in the cell; a lane still in flight stays BLOCKED until its suite is on the tree.
import assert from "node:assert/strict";
import { test } from "node:test";
import { directives, execute, judgePart as judgeAny, readMatrix } from "./run-matrix.mjs";

type Verdict = { status: string; cause?: string; passed?: number; skipped?: number };
const judgePart = (p: object, r: object) => judgeAny(p, r) as Verdict;
const real = { name: "server authorization", app: "lab", paths: ["tests/e2e/x/stack.test.ts"], lane: "ux-verify-final (UX-11)", real: { env: { INFRX_D_TASK: "l4" }, needs: "Docker, key l4" } };
/** A real part on a path that exists (this file): what execute decides before any suite is spawned. */
const here = { ...real, app: "app", paths: ["tests/ux/matrix/runner.test.ts"] };
const ok = (name: string) => ({ name, ok: true, skipped: false });
const todo = (name: string) => ({ name, ok: true, skipped: true });

test("UXV-M01 a real part not requested is BLOCKED naming --real and what it needs, and nothing is run", () => {
  const cache = new Map();
  const run = execute(here, cache, false);
  const v = judgePart(here, run);
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause ?? "", /--real/);
  assert.match(v.cause ?? "", /Docker, key l4/);
  assert.match(v.cause ?? "", /ux-verify-final \(UX-11\)/);
  assert.equal(cache.size, 0, "no suite was spawned");
});

test("UXV-M02 a requested real part whose port is closed is BLOCKED naming it, not run and never FAIL; a reachable one is judged by its cases", () => {
  const cache = new Map();
  const v = judgePart(here, execute({ ...here, real: { ...here.real, ports: [9] } }, cache, true));
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause ?? "", /nothing listens on 127\.0\.0\.1:9/);
  assert.equal(cache.size, 0, "no suite was spawned");
  assert.equal(judgePart(real, { cases: [ok("y")], code: 0, missing: [] }).status, "PASS");
  assert.equal(judgePart(real, { cases: [ok("y")], code: 0, missing: [], blocked: "x" }).status, "BLOCKED", "a blocked run is never judged by its cases");
});

test("UXV-M03 a part whose every case is NOT RUN is BLOCKED with the cases' own reasons, once each; one ran case is judged", () => {
  const reasons = new Map([
    ["E2E-E02 launch", "NOT RUN[SR-AP10-1]: the catalog answers 503"],
    ["E2E-E03 cancel", "NOT RUN[SR-AP10-1]: the catalog answers 503"],
  ]);
  const v = judgePart({ ...real, match: "^E2E-E0[23] " }, { cases: [todo("E2E-E02 launch"), todo("E2E-E03 cancel"), ok("E2E-E01 list")], code: 0, missing: [], reasons });
  assert.equal(v.status, "BLOCKED");
  assert.equal(v.cause?.split("NOT RUN[SR-AP10-1]").length, 2, `one reason, once: ${v.cause}`);
  assert.deepEqual([v.passed, v.skipped], [0, 2]);
  assert.equal(judgePart(real, { cases: [todo("E2E-E02 launch"), ok("E2E-E01 list")], code: 0, missing: [], reasons }).status, "PASS");
});

test("UXV-M04 directives: each SKIP or TODO case's reason by name, nested ones included; a plain ok has none", () => {
  const out = ["ok 1 - E2E-E j10 the flow", "    ok 2 - E2E-E02 launch # TODO NOT RUN[SR-AP10-1]: catalog 503", "ok 3 - S01 lists # SKIP LAB_V1M_REAL=1", "ok 4 - plain"].join("\n");
  assert.deepEqual([...directives(out)], [
    ["E2E-E02 launch", "NOT RUN[SR-AP10-1]: catalog 503"],
    ["S01 lists", "LAB_V1M_REAL=1"],
  ]);
});

type Part = { name: string; app?: string; paths?: string[]; match?: string; lane: string; blocked?: string; real?: { env: Record<string, string>; needs: string } };
test("UXV-M05 the final matrix: UX-10 and row 98 run their suites now they merged; every real part runs on its own key and never the E3A suite", () => {
  const parts: Part[] = readMatrix().journeys.flatMap((j: { parts: Part[] }) => j.parts);
  const releases = parts.find((p) => p.lane.startsWith("ux-lab-releases"));
  assert.deepEqual([releases?.app, releases?.paths], ["lab", ["tests/ux/releases"]], "UX-10 unblocks itself when its suite lands");
  const judge = parts.find((p) => p.lane.startsWith("lab-judge-runs"));
  assert.deepEqual([judge?.app, judge?.paths, judge?.blocked], ["lab", ["tests/c/judge/runs.test.ts"], undefined], "row 98 runs its suite");
  const reals = parts.filter((p) => p.real);
  assert.ok(reals.length >= 4, "lab-e2e and the V1M stack are matrix parts");
  for (const p of reals) {
    assert.ok(["l4", "lab-v1m"].includes(p.real!.env.INFRX_D_TASK), `${p.name} runs on its own key, never d1`);
    assert.ok(p.real!.needs, `${p.name} names what it needs`);
    assert.ok(p.paths!.every((x) => !x.startsWith("tests/e2e") || p.app === "lab"), `${p.name} never runs the App's E3A suite`);
  }
  for (const p of parts.filter((x) => x.paths && /^tests\/ux\/(operate|improve|evaluations|requests)$|^tests\/ux\/usage$/.test(x.paths.join()))) assert.ok(p.match, `${p.name}: a directory part is narrowed to its journey's cases`);
});

test("UXV-M06 a case its suite marks TODO FAIL[...] is a known product defect: FAIL with its reason while it fails, PASS once fixed, never BLOCKED", () => {
  const reasons = new Map([["UXV-A05 popups", "FAIL[WR-UXVF-3]: no reduced-motion rule"], ["E2E-E02 launch", "NOT RUN[SR-AP10-1]"]]);
  // node reports a failing TODO case "not ok ... # TODO": skipped, and not ok.
  const v = judgePart({ ...real, real: undefined }, { cases: [ok("UXV-A04 menu"), { name: "UXV-A05 popups", ok: false, skipped: true }], code: 0, missing: [], reasons });
  assert.equal(v.status, "FAIL");
  assert.match(v.cause ?? "", /WR-UXVF-3/);
  assert.equal(judgePart({ ...real, real: undefined }, { cases: [ok("UXV-A04 menu"), todo("E2E-E02 launch")], code: 0, missing: [], reasons }).status, "PASS", "a NOT RUN beside a pass is not a failure");
  const fixed = judgePart({ ...real, real: undefined }, { cases: [{ name: "UXV-A05 popups", ok: true, skipped: true }], code: 0, missing: [], reasons });
  assert.deepEqual([fixed.status, fixed.passed], ["PASS", 1], "once its fix lands the case passes");
});
