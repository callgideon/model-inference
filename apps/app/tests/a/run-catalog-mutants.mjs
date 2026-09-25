#!/usr/bin/env node
// R32 for A3: every invariant the A3 cases name must be killable.
//
// Each mutant in `catalog-mutants.json` is one edit to an A3 module (catalog, content, examples, or a page's
// source the copy guard reads), applied to a throwaway copy of the console. It is killed only when a
// case it DECLARES fails on an assertion. A stale `find`, a copy that fails to load, a failure in an
// undeclared case or a failure by exception is not a kill. Two self-checks prove the runner can
// tell a survivor and a broken copy from a kill.
//
// The copy is laid out as <tmp>/apps/app with `apps/infrx-api` and `research` symlinked to the
// real tree, because the suite reads the Python contract fixtures and the generated endpoint
// document by relative path. Nothing is written through the symlinks.
//
// Usage: node tests/a/run-catalog-mutants.mjs [--only ID,ID] [--timeout MS] [--keep]
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");
const repoRoot = resolve(appRoot, "../..");

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const only = flag("--only", null);
const timeoutMs = Math.max(1000, Number(flag("--timeout", "120000")) || 120000);
const keep = args.includes("--keep");

const SUITE = ["tests/a/catalog.test.ts", "tests/a/content.test.ts", "tests/a/examples.test.ts", "tests/a/fake-gateway.test.ts"];
const { mutants: MUTANTS } = JSON.parse(readFileSync(join(here, "catalog-mutants.json"), "utf8"));

const CATALOG = "app/(console)/models/catalog.ts";
const SELF = [
  {
    id: "SELF-NOOP",
    what: "an edit that changes nothing survives",
    file: CATALOG,
    find: "export const CATALOG_TIMEOUT_MS = 5000;",
    replace: "export const CATALOG_TIMEOUT_MS = 5000; // self-check no-op",
    cases: ["no configured origin is unavailable, and nothing is fetched"],
    expect: "survived",
  },
  {
    id: "SELF-BROKEN",
    what: "a file that does not parse is a runner error, not a kill",
    file: CATALOG,
    find: "export const CATALOG_TIMEOUT_MS = 5000;",
    replace: "export const CATALOG_TIMEOUT_MS = ;",
    cases: ["no configured origin is unavailable, and nothing is fetched"],
    expect: "runner-error",
  },
];

function prepareCopy() {
  const root = mkdtempSync(join(tmpdir(), "a3-mutants-"));
  const app = join(root, "apps", "app");
  mkdirSync(dirname(app), { recursive: true });
  cpSync(appRoot, app, {
    recursive: true,
    dereference: false,
    filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
  });
  symlinkSync(join(repoRoot, "apps", "infrx-api"), join(root, "apps", "infrx-api"));
  symlinkSync(join(repoRoot, "research"), join(root, "research"));
  return { root, app };
}

/** The failing cases and how each failed: a kill has to be an assertion, not a stray exception. */
function failingCases(out) {
  const cases = [];
  const pattern = /^ *not ok \d+ - (.*)$/gm;
  for (let match = pattern.exec(out); match !== null; match = pattern.exec(out)) {
    const rest = out.slice(match.index + match[0].length);
    const end = rest.search(/^ *\.\.\.$/m);
    const diagnostic = end === -1 ? rest : rest.slice(0, end);
    cases.push({ name: match[1].trim(), how: /code: 'ERR_ASSERTION'/.test(diagnostic) ? "assertion" : "error" });
  }
  return cases;
}

const passingCases = (out) => [...out.matchAll(/^ *ok \d+ - (.*)$/gm)].map((match) => match[1].trim());

function runSuite(app) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...SUITE], {
      cwd: app,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutMs);
    child.stdout.on("data", (chunk) => (out += chunk));
    child.stderr.on("data", (chunk) => (out += chunk));
    child.on("close", (code) => {
      clearTimeout(timer);
      done({ code, out, timedOut, failed: failingCases(out), passed: passingCases(out) });
    });
  });
}

function applyMutant(app, mutant) {
  const pristine = readFileSync(join(appRoot, mutant.file), "utf8");
  const occurrences = pristine.split(mutant.find).length - 1;
  if (occurrences === 0) return { ok: false, why: "its `find` text is not in the source any more" };
  if (occurrences > 1) return { ok: false, why: `its \`find\` text appears ${occurrences} times` };
  writeFileSync(join(app, mutant.file), pristine.replace(mutant.find, mutant.replace));
  return { ok: true };
}

function classify(mutant, run) {
  if (run.timedOut) return { outcome: "runner-error", why: "the suite did not finish in time" };
  if (!/^# tests \d+/m.test(run.out)) return { outcome: "runner-error", why: "the suite produced no TAP summary" };
  if (run.code !== 0 && run.failed.length === 0) return { outcome: "runner-error", why: "the suite failed without naming a case" };
  if (run.code === 0) return { outcome: "survived", why: "the suite passed" };
  if (run.passed.length === 0) return { outcome: "runner-error", why: "every case failed, so the copy is broken" };
  // A module that does not load fails its whole file as one case named by the file's path.
  if (run.failed.some((entry) => entry.name.startsWith("tests/"))) {
    return { outcome: "runner-error", why: "a test file failed to load" };
  }
  const matched = run.failed.filter((entry) => mutant.cases.includes(entry.name));
  if (matched.length === 0) {
    return { outcome: "survived", why: `failed, but not in a declared case (${run.failed.map((c) => c.name).join("; ")})` };
  }
  const byAssertion = matched.filter((entry) => entry.how === "assertion");
  if (byAssertion.length === 0) return { outcome: "runner-error", why: "the declared case failed by exception, not by assertion" };
  return { outcome: "killed", by: byAssertion[0].name };
}

async function judge(mutant) {
  const { root, app } = prepareCopy();
  try {
    const applied = applyMutant(app, mutant);
    if (!applied.ok) return { outcome: "stale", why: applied.why };
    return classify(mutant, await runSuite(app));
  } finally {
    if (!keep) rmSync(root, { recursive: true, force: true });
  }
}

const selected = only === null ? MUTANTS : MUTANTS.filter((m) => only.split(",").includes(m.id));
if (selected.length === 0) {
  console.error(`--only ${only} matched no mutant of ${MUTANTS.length}`);
  process.exit(1);
}
let selfFailures = 0;
let survivors = 0;
for (const mutant of SELF) {
  const result = await judge(mutant);
  const ok = result.outcome === mutant.expect;
  if (!ok) selfFailures += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${mutant.id} self-check expected ${mutant.expect}, got ${result.outcome}${result.why ? ` (${result.why})` : ""}`);
}
for (const mutant of selected) {
  const result = await judge(mutant);
  if (result.outcome !== "killed") survivors += 1;
  console.log(
    `${result.outcome === "killed" ? "killed " : result.outcome.toUpperCase()} ${mutant.id} ${mutant.what}` +
      `${result.outcome === "killed" ? ` — by "${result.by}"` : ` — ${result.why}`}`,
  );
}
console.log(`\n${SELF.length} self-checks, ${SELF.length - selfFailures} as expected; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(selfFailures === 0 && survivors === 0 ? 0 : 1);
