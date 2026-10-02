#!/usr/bin/env node
// UX-11 final acceptance mutant runner (R32): every decision the UXV-M (matrix runner, final matrix) and
// UXV-A (App accessibility pass: a11y-probe.js and the shell) cases claim is one edit that a case it names
// must fail by ASSERTION; every passing UXV-M/UXV-A case must be named. A mutant runs on a copy of the
// App (node_modules linked); the UXV-A cases serve the copy's synthetic harness (next dev + Chromium).
// The Lab's UXV-L cases have their own runner (apps/lab/tests/ux/matrix/run-mutants.mjs).
// Usage: node tests/ux/matrix/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const app = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const RUNNER = ["tests/ux/matrix/runner.test.ts"];
const A11Y = ["tests/ux/matrix/a11y.check.ts"];
const R = "tests/ux/matrix/run-matrix.mjs";
const J = "tests/ux/matrix/matrix.json";
const P = "tests/ux/matrix/a11y-probe.js";
const m = (id, what, file, find, replace, cases, suite) => ({ id, what, file, find, replace, cases, suite });

const C = {
  m01: "UXV-M01 a real part not requested is BLOCKED naming --real and what it needs, and nothing is run",
  m02: "UXV-M02 a requested real part whose port is closed is BLOCKED naming it, not run and never FAIL; a reachable one is judged by its cases",
  m03: "UXV-M03 a part whose every case is NOT RUN is BLOCKED with the cases' own reasons, once each; one ran case is judged",
  m04: "UXV-M04 directives: each SKIP or TODO case's reason by name, nested ones included; a plain ok has none",
  m05: "UXV-M05 the final matrix: UX-10 and row 98 run their suites now they merged; every real part runs on its own key and never the E3A suite",
  m06: "UXV-M06 a case its suite marks TODO FAIL[...] is a known product defect: FAIL with its reason while it fails, PASS once fixed, never BLOCKED",
  a01: "UXV-A01 the probe reports each planted defect: low-contrast text, an unnamed button, a sideways overflow, a sliding, a slid and a moving box; an inactive control, a hidden spinner and a fade are exempt",
  a02: "UXV-A02 at 200% zoom (640×400 CSS px) the shell never scrolls sideways; Tab reaches every control in view, uncovered and with a focus indicator; the menu opens and closes by keyboard",
  a03: "UXV-A03 every control is named and all text meets AA contrast, at 1440 px and with the 390 px menu open",
  a04: "UXV-A04 reduced motion: opening and closing the mobile menu moves nothing",
  a05: "UXV-A05 reduced motion: the App's dialog and dropdown menu popups, as committed, move nothing",
};

const MUTANTS = [
  m("UXV-X01", "a real part not requested runs anyway", R, "if (real && !wantReal) return", "if (false) return", [C.m01], RUNNER),
  m("UXV-X02", "the not-requested cause drops what the gate needs", R, "rerun with --real (needs ${real.needs})", "rerun with --real", [C.m01], RUNNER),
  m("UXV-X03", "a closed port is not checked", R, ".filter((p) => !listening(p));", ".filter(() => false);", [C.m02], RUNNER),
  m("UXV-X04", "a blocked run is judged by its cases", R, "  if (run.blocked) return", "  if (false) return", [C.m01, C.m02], RUNNER),
  m("UXV-X05", "the NOT RUN reasons are dropped from the cell", R, '${why.length ? `: ${why.join("; ")}` : ""}', "", [C.m03], RUNNER),
  m("UXV-X06", "a reason repeats once per case", R, "const why = [...new Set(", "const why = [...(", [C.m03], RUNNER),
  m("UXV-X07", "nested directives are missed", R, "/^ *(?:not )?ok \\d+ - (.*?) # (?:SKIP|TODO)", "/^(?:not )?ok \\d+ - (.*?) # (?:SKIP|TODO)", [C.m04], RUNNER),
  m("UXV-X08", "a SKIP reason is missed", R, "# (?:SKIP|TODO)", "# (?:TODO)", [C.m04], RUNNER),
  m("UXV-X09", "a TODO FAIL[...] reads as a skip", R, "/^FAIL\\[/.test(", "/^NEVER\\[/.test(", [C.m06], RUNNER),
  m("UXV-X26", "a fixed defect still fails", R, "const known = cases.filter((c) => flagged(c) && !c.ok);", "const known = cases.filter((c) => flagged(c));", [C.m06], RUNNER),
  m("UXV-X27", "a fixed defect reads as skipped", R, "c.skipped && !flagged(c)).length;", "c.skipped).length - known.length;", [C.m06], RUNNER),
  m("UXV-X10", "a known defect's FAIL drops its reason", R, '${known.map((c) => `; ${run.reasons.get(c.name)}`).join("")}', "", [C.m06], RUNNER),
  m("UXV-X11", "UX-10's part is declared blocked forever", J, '"app": "lab", "paths": ["tests/ux/releases"]', '"blocked": "batch 3"', [C.m05], RUNNER),
  m("UXV-X12", "a real part runs on d1", J, '"INFRX_D_TASK": "lab-v1m"', '"INFRX_D_TASK": "d1"', [C.m05], RUNNER),
  m("UXV-X13", "a directory part loses its journey narrowing", J, ', "match": "^UX06-"', "", [C.m05], RUNNER),
  m("UXV-X14", "row 98 reads as blocked again", J, '"paths": ["tests/c/judge/runs.test.ts"]', '"blocked": "row 98"', [C.m05], RUNNER),
  m("UXV-X15", "sideways overflow is never reported", P, "overflow: document.documentElement.scrollWidth > window.innerWidth", "overflow: false", [C.a01], A11Y),
  m("UXV-X16", "an unnamed control is never reported", P, 'controls.filter((el) => name(el) === "")', "controls.filter(() => false)", [C.a01], A11Y),
  m("UXV-X17", "contrast is judged at 1:1", P, "const need = large ? 3 : 4.5;", "const need = 1;", [C.a01], A11Y),
  m("UXV-X18", "an inactive control is judged for contrast", P, ', :disabled, [aria-disabled=true]")) continue;', '")) continue;', [C.a01], A11Y),
  m("UXV-X19", "a finished (declared) slide is not motion", P, "    if (moving.length > 0) motion.push(`${label(el)} declares", "    if (false) motion.push(`${label(el)} declares", [C.a01], A11Y),
  m("UXV-X20", "a running transition is not motion", P, "if (!exempt(el) && el instanceof Element && boxed(el) && ms > 10", "if (false && ms > 10", [C.a01], A11Y),
  m("UXV-X21", "a hidden pending spinner counts as motion", P, '!el || el.closest("[role=status], [aria-busy=true], [aria-hidden=true]") !== null', "!el", [C.a01], A11Y),
  m("UXV-X22", "an opacity fade counts as motion", P, "const MOVES = /^(transform|", "const MOVES = /^(opacity|transform|", [C.a01], A11Y),
  m("UXV-X23", "the menu button loses its accessible name", "components/sidebar.tsx", '<Button variant="ghost" size="icon-sm" aria-label="Open menu" />', '<Button variant="ghost" size="icon-sm" />', [C.a02, C.a03], A11Y),
  m("UXV-X25", "the focus ring is removed from the App's buttons", "components/ui/button.tsx", "focus-visible:ring-3 ", "", [C.a02], A11Y),
  m("UXV-X28", "the reduced-motion rule lets animations run", "app/globals.css", "    animation-duration: 0.01ms !important;\n", "", [C.a04, C.a05], A11Y),
];

function failed(out) {
  const cases = [];
  const re = /^( *)not ok \d+ - (.*)$/gm;
  for (let hit = re.exec(out); hit !== null; hit = re.exec(out)) {
    const rest = out.slice(hit.index + hit[0].length);
    const end = rest.search(new RegExp(`^${hit[1]}  \\.\\.\\.$`, "m"));
    cases.push({ name: hit[2].replace(/ # TODO.*$/, "").trim(), assertion: /code: 'ERR_ASSERTION'/.test(end === -1 ? rest : rest.slice(0, end)), todo: / # TODO/.test(hit[2]) });
  }
  return cases.filter((c) => !c.todo);
}

function tap(cwd, suite) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...suite], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    child.on("close", (code) => done({ code, out }));
  });
}

async function judge(mutant) {
  const pristine = readFileSync(join(app, mutant.file), "utf8");
  const hits = pristine.split(mutant.find).length - 1;
  if (hits !== 1) return `STALE (find matches ${hits} times)`;
  const root = mkdtempSync(join(tmpdir(), "app-uxv-mutants-"));
  try {
    const copy = join(root, "apps/app");
    mkdirSync(join(root, "apps"), { recursive: true });
    cpSync(app, copy, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(app.length)) });
    symlinkSync(join(app, "node_modules"), join(copy, "node_modules"), "dir");
    writeFileSync(join(copy, mutant.file), pristine.replace(mutant.find, () => mutant.replace));
    const { code, out } = await tap(copy, mutant.suite);
    const fails = failed(out);
    if (code === 0 && fails.length === 0) return "SURVIVED (suite passed)";
    if (fails.some((f) => /\.(test|check)\.ts$/.test(f.name))) return "RUNNER-ERROR (a test file did not load)";
    const hit = fails.find((f) => f.assertion && mutant.cases.includes(f.name));
    return hit ? `killed by "${hit.name}"` : `SURVIVED (failed only: ${fails.map((f) => `${f.name}${f.assertion ? "" : " [error]"}`).join("; ")})`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

const declared = new Set(MUTANTS.flatMap((x) => x.cases));
const baseline = await tap(app, [...RUNNER, ...A11Y]);
const passed = [...baseline.out.matchAll(/^ *ok \d+ - (.*?)( # (TODO|SKIP).*)?$/gm)].filter((x) => !x[2]).map((x) => x[1].trim());
const cases = passed.filter((name) => /^UXV-[MA]\d\d /.test(name));
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !passed.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
];
const selected = only === null ? MUTANTS : MUTANTS.filter((x) => only.includes(x.id));
if (selected.length === 0) problems.push("--only matched no mutant");
for (const problem of problems) console.log(`FAIL ${problem}`);
let survivors = 0;
for (const mutant of selected) {
  const verdict = await judge(mutant);
  if (!verdict.startsWith("killed")) survivors += 1;
  console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
}
console.log(`\n${cases.length} cases, all named: ${problems.length === 0}; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(problems.length === 0 && survivors === 0 ? 0 : 1);
