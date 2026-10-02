// UX-11 final acceptance: the matrix runner's real-gate parts and NOT RUN causes (run-matrix.mjs), and
// the final matrix it reads. Failure oracle: a real gate that was not run, or could not be, must never
// read as PASS or FAIL - it is BLOCKED naming what it needs; a case a suite reports NOT RUN keeps its own
// reason in the cell; a lane still in flight stays BLOCKED until its suite is on the tree.
import assert from "node:assert/strict";
import { test } from "node:test";
import { directives, judgePart as judgeAny, readMatrix } from "./run-matrix.mjs";

type Verdict = { status: string; cause?: string; passed?: number; skipped?: number };
const judgePart = (p: object, r: object) => judgeAny(p, r) as Verdict;
const real = { name: "server authorization", app: "lab", paths: ["tests/e2e/x/stack.test.ts"], lane: "ux-verify-final (UX-11)", real: { env: { INFRX_D_TASK: "l4" }, needs: "Docker, key l4" } };
const ok = (name: string) => ({ name, ok: true, skipped: false });
const todo = (name: string) => ({ name, ok: true, skipped: true });

test("UXV-M01 a real part not requested is BLOCKED naming --real and what it needs, whatever its cases say", () => {
  const v = judgePart(real, { cases: [ok("E2E-O01 a")], code: 0, missing: [], blocked: "real gate not requested: rerun with --real (needs Docker, key l4)" });
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause ?? "", /--real/);
  assert.match(v.cause ?? "", /Docker, key l4/);
  assert.match(v.cause ?? "", /ux-verify-final \(UX-11\)/);
});

test("UXV-M02 a real part whose prerequisite is absent is BLOCKED, not FAIL, even with a failed run", () => {
  const v = judgePart(real, { cases: [{ name: "x", ok: false, skipped: false }], code: 1, missing: [], blocked: "nothing listens on 127.0.0.1:57540" });
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause ?? "", /57540/);
  assert.equal(judgePart(real, { cases: [ok("y")], code: 0, missing: [] }).status, "PASS", "a requested, reachable real part is judged by its cases");
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
test("UXV-M05 the final matrix: UX-10 and row 98 stay BLOCKED until they merge; every real part runs on its own key and never the E3A suite", () => {
  const parts: Part[] = readMatrix().journeys.flatMap((j: { parts: Part[] }) => j.parts);
  const releases = parts.find((p) => p.lane.startsWith("ux-lab-releases"));
  assert.deepEqual([releases?.app, releases?.paths], ["lab", ["tests/ux/releases"]], "UX-10 unblocks itself when its suite lands");
  assert.ok(parts.some((p) => p.lane.startsWith("lab-judge-runs") && p.blocked), "row 98 is BLOCKED[lab-judge-runs]");
  const reals = parts.filter((p) => p.real);
  assert.ok(reals.length >= 4, "lab-e2e and the V1M stack are matrix parts");
  for (const p of reals) {
    assert.ok(["l4", "lab-v1m"].includes(p.real!.env.INFRX_D_TASK), `${p.name} runs on its own key, never d1`);
    assert.ok(p.real!.needs, `${p.name} names what it needs`);
    assert.ok(p.paths!.every((x) => !x.startsWith("tests/e2e") || p.app === "lab"), `${p.name} never runs the App's E3A suite`);
  }
  for (const p of parts.filter((x) => x.paths && /^tests\/ux\/(operate|improve|evaluations|requests)$|^tests\/ux\/usage$/.test(x.paths.join()))) assert.ok(p.match, `${p.name}: a directory part is narrowed to its journey's cases`);
});
