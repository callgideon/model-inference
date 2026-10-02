#!/usr/bin/env node
// UX-11: the UX-T01–T13 acceptance matrix (research/design/v1/07-handoff.md) on one SHA. matrix.json maps
// each journey to parts: a runnable part names an app (App or Lab) and the synthetic-harness suites that
// carry it; a blocked part names its cause. Each runnable part runs `node --test` in its app; the
// verdict is per part, per journey and overall (FAIL > INVALID > BLOCKED > PASS, as the gates rank):
//   - a suite path that is not on the tree yet is BLOCKED naming the lane that owns it;
//   - no case run (none matched, or every one skipped) is BLOCKED, never PASS;
//   - a failed case or a non-zero exit is FAIL;
//   - a dirty tree is INVALID (the run would not be the SHA's); a fixture reference that no longer
//     resolves is FAIL;
//   - a case its suite reports NOT RUN (TODO/SKIP) keeps its reason in the cell.
// By default it runs only the apps' own fixture suites: no Docker, no network service, no screenshots.
// A `real` part (the Lab's real-route stack suites: lab-e2e on l4, the V1M stack on lab-v1m + t2i's
// ClickHouse) runs only with --real, after its `prepare` (once per app), with its env and one test file
// at a time; without --real, or with a declared port not listening, it is BLOCKED naming what it needs.
// It never invokes the App's real E3A suite (apps/app/tests/e2e), whose capture policy stays off.
// Usage (from anywhere): node apps/app/tests/ux/matrix/run-matrix.mjs [--real] [--out verdict.json] [--only T01,T02]
// Exit: 0 PASS, 1 FAIL, 3 BLOCKED, 4 INVALID.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../../../..");

export const RANK = ["PASS", "BLOCKED", "INVALID", "FAIL"];
export const worst = (statuses) => statuses.reduce((a, b) => (RANK.indexOf(b) > RANK.indexOf(a) ? b : a), "PASS");

export const readMatrix = () => JSON.parse(readFileSync(join(here, "matrix.json"), "utf8"));

/** Every `ok`/`not ok` line of a TAP report, nested ones included. Node escapes `#` in names as `\#`. */
export function parseTap(out) {
  return [...out.matchAll(/^ *(not )?ok \d+ - (.*?)(?: # (SKIP|TODO)\b.*)?$/gm)].map(([, not, name, directive]) => ({
    name,
    ok: !not,
    skipped: directive !== undefined,
  }));
}

/** Each SKIP/TODO case's reason by name (parseTap keeps only that it was skipped). */
export function directives(out) {
  return new Map([...out.matchAll(/^ *(?:not )?ok \d+ - (.*?) # (?:SKIP|TODO)\b ?(.*)$/gm)].map(([, name, why]) => [name, why.trim()]));
}

/** One part's verdict from its run: { cases, code, missing, reasons?, blocked? } (missing = declared
 * paths not on the tree; blocked = a real part not requested or its prerequisite absent). */
export function judgePart(part, run) {
  if (part.blocked) return { status: "BLOCKED", cause: `${part.blocked} (unblocks: ${part.lane})` };
  if (run.blocked) return { status: "BLOCKED", cause: `${run.blocked} (owner: ${part.lane})` };
  if (run.missing.length > 0) return { status: "BLOCKED", cause: `${part.lane} has not merged ${run.missing.join(", ")}` };
  const match = part.match ? new RegExp(part.match) : null;
  const cases = run.cases.filter((c) => match === null || match.test(c.name));
  const skipped = cases.filter((c) => c.skipped).length;
  const failed = cases.filter((c) => !c.ok && !c.skipped).length;
  const counts = { passed: cases.length - skipped - failed, failed, skipped };
  if (failed > 0 || run.code !== 0) return { status: "FAIL", cause: `${failed} failed, exit ${run.code}`, ...counts };
  const why = [...new Set(cases.filter((c) => c.skipped).map((c) => run.reasons?.get(c.name)).filter(Boolean))];
  if (counts.passed === 0) return { status: "BLOCKED", cause: `no case ran (${skipped} skipped${why.length ? `: ${why.join("; ")}` : ""}; owner ${part.lane})`, ...counts };
  return { status: "PASS", ...counts };
}

/** A fixture-register entry: a committed source that must still hold its symbol, or a gap with an owner. */
export function checkFixture(entry, root = repo) {
  if (entry.gap) return "GAP";
  const file = join(root, entry.path);
  return existsSync(file) && readFileSync(file, "utf8").includes(entry.contains) ? "COVERED" : "DANGLING";
}

export function overall(journeys, fixtures, dirty) {
  return worst([
    ...journeys.map((j) => j.status),
    ...(fixtures.some((f) => f.status === "DANGLING") ? ["FAIL"] : []),
    ...(dirty ? ["INVALID"] : []),
  ]);
}

const git = (...args) => spawnSync("git", ["-C", repo, ...args], { encoding: "utf8" }).stdout.trim();

const listening = (port) =>
  spawnSync(process.execPath, ["-e", `require("net").connect(${port}, "127.0.0.1").on("connect", () => process.exit(0)).on("error", () => process.exit(1))`]).status === 0;

function execute(part, cache, wantReal) {
  if (part.blocked) return { cases: [], code: 0, missing: [] };
  const cwd = join(repo, "apps", part.app);
  const missing = part.paths.filter((p) => !existsSync(join(cwd, p)));
  if (missing.length > 0) return { cases: [], code: 0, missing };
  const real = part.real;
  if (real && !wantReal) return { cases: [], code: 0, missing, blocked: `real gate not requested: rerun with --real (needs ${real.needs})` };
  const closed = (real?.ports ?? []).filter((p) => !listening(p));
  if (closed.length > 0) return { cases: [], code: 0, missing, blocked: `needs ${real.needs}: nothing listens on ${closed.map((p) => `127.0.0.1:${p}`).join(", ")}` };
  if (real?.prepare && !cache.has(`prepare:${part.app}:${real.prepare.join(" ")}`)) {
    const r = spawnSync(real.prepare[0], real.prepare.slice(1), { cwd, encoding: "utf8", maxBuffer: 1 << 28 });
    cache.set(`prepare:${part.app}:${real.prepare.join(" ")}`, r.status);
  }
  if (real?.prepare && cache.get(`prepare:${part.app}:${real.prepare.join(" ")}`) !== 0) return { cases: [], code: 1, missing };
  const files = part.paths.map((p) => (statSync(join(cwd, p)).isDirectory() ? `${p}/**/*.test.ts` : p));
  const key = `${part.app}:${JSON.stringify(real?.env ?? {})}:${files.join(" ")}`;
  if (!cache.has(key)) {
    const args = real ? ["--test-concurrency=1"] : [];
    const env = { ...process.env, ...(real?.env ?? {}) };
    const r = spawnSync(process.execPath, ["--test", "--test-reporter=tap", ...args, ...files], { cwd, env, encoding: "utf8", maxBuffer: 1 << 28 });
    const out = `${r.stdout}\n${r.stderr}`;
    cache.set(key, { cases: parseTap(out), code: r.status ?? 1, reasons: directives(out) });
  }
  return { ...cache.get(key), missing };
}

function main(argv) {
  const flag = (name) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : null);
  const only = flag("--only")?.split(",") ?? null;
  const matrix = readMatrix();
  const sha = git("rev-parse", "HEAD");
  const dirty = git("status", "--porcelain") !== "";
  const cache = new Map();
  const journeys = matrix.journeys
    .filter((j) => only === null || only.includes(j.id))
    .map((j) => {
      const parts = j.parts.map((p) => ({ ...p, ...judgePart(p, execute(p, cache, argv.includes("--real"))) }));
      return { id: j.id, title: j.title, status: worst(parts.map((p) => p.status)), parts };
    });
  const fixtures = matrix.fixtures.map((f) => ({ ...f, status: checkFixture(f) }));
  const verdict = overall(journeys, fixtures, dirty);
  const report = { verdict, sha, dirty, real: argv.includes("--real"), node: process.version, at: new Date().toISOString(), journeys, fixtures };
  const out = flag("--out");
  if (out) writeFileSync(out, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`UX matrix at ${sha}${dirty ? " (DIRTY TREE)" : ""}: ${verdict}\n`);
  console.log("| Journey | Part | Status | Passed/failed/skipped | Cause |\n|---|---|---|---|---|");
  for (const j of journeys)
    for (const p of j.parts)
      console.log(`| ${j.id} ${j.title} | ${p.name} (${p.app ?? "-"}) | ${p.status} | ${p.passed ?? "-"}/${p.failed ?? "-"}/${p.skipped ?? "-"} | ${p.cause ?? ""} |`);
  const count = (s) => fixtures.filter((f) => f.status === s).length;
  console.log(`\nFixtures: ${count("COVERED")} covered, ${count("GAP")} gaps, ${count("DANGLING")} dangling`);
  for (const f of fixtures.filter((x) => x.status === "DANGLING")) console.log(`DANGLING ${f.dimension} / ${f.case}: ${f.path} lacks ${JSON.stringify(f.contains)}`);
  return { PASS: 0, FAIL: 1, BLOCKED: 3, INVALID: 4 }[verdict];
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main(process.argv.slice(2)));
