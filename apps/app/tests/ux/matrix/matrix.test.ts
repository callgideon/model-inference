// UX-11 preparation: the T01–T13 matrix runner's decisions (run-matrix.mjs) and the matrix it reads.
// Failure oracle: a skipped, missing or unmerged journey must never read as PASS, a failing one never as
// anything but FAIL, and a run on a dirty tree never as a verdict for its SHA.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { checkFixture, judgePart, overall, parseTap, readMatrix, worst } from "./run-matrix.mjs";

const run = (cases: { name: string; ok: boolean; skipped: boolean }[], code = 0, missing: string[] = []) => ({ cases, code, missing });
const ok = (name: string) => ({ name, ok: true, skipped: false });
const part = { name: "fixture", app: "app", paths: ["tests/ux/x.test.ts"], lane: "ux-x (UX-99)" };

test("UXM-01 TAP: ok, not ok, SKIP and TODO are read; an escaped hash-SKIP inside a name is not a directive", () => {
  const out = [
    "TAP version 13",
    "# Subtest: a",
    "ok 1 - a passes",
    "not ok 2 - b fails",
    "ok 3 - c is skipped # SKIP no docker",
    "not ok 4 - d is todo # TODO later",
    "    ok 1 - nested e",
    "ok 5 - f mentions \\# SKIP in its name",
  ].join("\n");
  assert.deepEqual(parseTap(out), [
    { name: "a passes", ok: true, skipped: false },
    { name: "b fails", ok: false, skipped: false },
    { name: "c is skipped", ok: true, skipped: true },
    { name: "d is todo", ok: false, skipped: true },
    { name: "nested e", ok: true, skipped: false },
    { name: "f mentions \\# SKIP in its name", ok: true, skipped: false },
  ]);
});

test("UXM-02 a declared-blocked part is BLOCKED with its cause and the lane that unblocks it, whatever a run says", () => {
  const v = judgePart({ name: "real", lane: "api-frontends-app (AP-09)", blocked: "needs the operator window" }, run([ok("x")]));
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause, /needs the operator window/);
  assert.match(v.cause, /api-frontends-app \(AP-09\)/);
});

test("UXM-03 a part whose suite has not merged is BLOCKED naming the lane and the missing path", () => {
  const v = judgePart(part, run([], 0, ["tests/ux/x.test.ts"]));
  assert.equal(v.status, "BLOCKED");
  assert.match(v.cause, /ux-x \(UX-99\)/);
  assert.match(v.cause, /tests\/ux\/x\.test\.ts/);
});

test("UXM-04 a skip is never a pass: no case, or only skipped cases, is BLOCKED", () => {
  assert.equal(judgePart(part, run([])).status, "BLOCKED");
  const v = judgePart(part, run([{ name: "s", ok: true, skipped: true }]));
  assert.equal(v.status, "BLOCKED");
  assert.equal(v.skipped, 1);
  assert.equal(v.passed, 0);
  assert.equal(judgePart(part, run([ok("p"), { name: "s", ok: true, skipped: true }])).status, "PASS");
});

test("UXM-05 a failed case, or a suite that exits non-zero, is FAIL", () => {
  assert.equal(judgePart(part, run([ok("p"), { name: "f", ok: false, skipped: false }], 1)).status, "FAIL");
  assert.equal(judgePart(part, run([ok("p")], 1)).status, "FAIL");
  assert.equal(judgePart(part, run([ok("p"), { name: "f", ok: false, skipped: false }], 0)).status, "FAIL");
  const v = judgePart(part, run([ok("p"), ok("q")]));
  assert.deepEqual([v.status, v.passed, v.failed], ["PASS", 2, 0]);
});

test("UXM-06 a part's match selects its cases: unmatched cases neither pass nor block it", () => {
  const scoped = { ...part, match: "^UX00-K0[12] " };
  assert.equal(judgePart(scoped, run([ok("UX00-S01 x")])).status, "BLOCKED");
  const v = judgePart(scoped, run([ok("UX00-K01 a"), ok("UX00-K02 b"), ok("UX00-S01 c")]));
  assert.deepEqual([v.status, v.passed], ["PASS", 2]);
});

test("UXM-07 the verdict ranks FAIL > INVALID > BLOCKED > PASS; a dirty tree is INVALID; a dangling fixture fails it", () => {
  assert.equal(worst(["PASS", "BLOCKED", "INVALID"]), "INVALID");
  assert.equal(worst(["INVALID", "FAIL", "BLOCKED"]), "FAIL");
  assert.equal(worst(["PASS", "BLOCKED"]), "BLOCKED");
  assert.equal(worst([]), "PASS");
  const pass = [{ status: "PASS" }];
  assert.equal(overall(pass, [{ status: "COVERED" }, { status: "GAP" }], false), "PASS");
  assert.equal(overall(pass, [], true), "INVALID");
  assert.equal(overall(pass, [{ status: "DANGLING" }], false), "FAIL");
});

test("UXM-08 a fixture reference is COVERED only when its file exists and holds its symbol; a gap stays a GAP", () => {
  const root = mkdtempSync(join(tmpdir(), "uxm-"));
  mkdirSync(join(root, "a"));
  writeFileSync(join(root, "a/f.ts"), 'export const S = { state: "signed_out" };\n');
  assert.equal(checkFixture({ path: "a/f.ts", contains: 'state: "signed_out"' }, root), "COVERED");
  assert.equal(checkFixture({ path: "a/f.ts", contains: 'state: "ready"' }, root), "DANGLING");
  assert.equal(checkFixture({ path: "a/missing.ts", contains: "x" }, root), "DANGLING");
  assert.equal(checkFixture({ gap: "ux-app-2 (UX-07)" }, root), "GAP");
});

test("UXM-09 the matrix declares T01..T13 once each; every part names a lane and either suite paths or its blocking cause", () => {
  const m = readMatrix();
  assert.deepEqual(m.journeys.map((j: { id: string }) => j.id), Array.from({ length: 13 }, (_, i) => `T${String(i + 1).padStart(2, "0")}`));
  for (const j of m.journeys) {
    assert.ok(j.parts.length > 0, `${j.id} has no part`);
    for (const p of j.parts) {
      assert.ok(p.lane, `${j.id}/${p.name} names no lane`);
      assert.ok(p.blocked ? !p.paths : p.paths?.length && ["app", "lab"].includes(p.app), `${j.id}/${p.name} is neither runnable nor blocked`);
    }
  }
  for (const f of m.fixtures) assert.ok(f.gap ? !f.path : f.path && f.contains, `fixture ${f.dimension}/${f.case} is neither a source nor a gap`);
});
