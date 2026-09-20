#!/usr/bin/env node
// R32/R40: the conformance suite's own test.
//
// Every invariant a conformance case names must be *killable*: if a single-edit change to the
// implementation can break the invariant and the suite still passes, the suite is claiming something
// it does not check. This runner applies each declared mutant to a temporary copy of the console and
// requires it to be killed by a NAMED case of the EXPORTED conformance functions — not the fake-only
// tests, because those are not what track C runs.
//
// What counts as a kill (R40: the runner must not be able to report a false one):
//
//   - at least one `not ok` line naming a case, and
//   - that case is one of the mutant's declared `cases`.
//
// A non-zero exit on its own is NOT a kill. A syntax error, a module that throws on load, a construct
// Node's type stripping rejects, a hang, or a suite that fails without naming a case are all
// RUNNER-ERROR: the mutant told us nothing, and the run fails. A mutant may legitimately kill by
// *throwing* — `kills_by: "throw"` declares that — but it still has to fail a declared case.
//
// Run with `pnpm test:mutants`. Deliberately not part of `pnpm test`: one Node process per mutant.
//
// Usage: node tests/contracts/run-mutants.mjs [--only ID,ID] [--jobs N] [--timeout MS] [--keep]
//        node tests/contracts/run-mutants.mjs --self-test
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const jobs = Math.max(1, Number(flag("--jobs", "4")) || 4);
const timeoutMs = Math.max(1000, Number(flag("--timeout", "120000")) || 120000);
const keep = args.includes("--keep");
const selfTest = args.includes("--self-test");

/**
 * The entry point each run executes: only the exported conformance functions, against the fake.
 * Written into the copy so a mutant is judged by what C would run and nothing else.
 */
const ENTRY = `import { runConsoleServicesConformance } from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";

runConsoleServicesConformance(() => {
  const services = createFakeConsoleServices();
  return { services, sessions: services.sessions, ids: services.ids };
}, "mutation target");
`;

function prepareCopy() {
  const root = mkdtempSync(join(tmpdir(), "f2ts-mutants-"));
  const app = join(root, "app");
  cpSync(appRoot, app, {
    recursive: true,
    dereference: false,
    filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
  });
  symlinkSync(join(appRoot, "node_modules"), join(app, "node_modules"), "dir");
  writeFileSync(join(app, "tests/contracts/__conformance-only.test.ts"), ENTRY);
  return { root, app };
}

/** Names of the failing subtests: how a *case* reports a failure in TAP. */
function failingCases(out) {
  return [...out.matchAll(/^ {4}not ok \d+ - (.*)$/gm)].map((match) => match[1].trim());
}

function passingCases(out) {
  return [...out.matchAll(/^ {4}ok \d+ - (.*)$/gm)].map((match) => match[1].trim());
}

/**
 * Why the copy did not run as a suite: a parse or load failure, a stripping rejection, or a process
 * that died without producing any case-level TAP. The test is *structural* — no named case failed —
 * and never a search for "SyntaxError" in the output, because a legitimate mutant can make a case
 * throw a SyntaxError (an accepted exponent reaching `BigInt`), and that is a kill, not an error.
 */
function loadFailureReason(out) {
  if (/ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX|ERR_INVALID_TYPESCRIPT_SYNTAX|ERR_UNKNOWN_FILE_EXTENSION/.test(out)) {
    return "Node's type stripping rejected the mutated file";
  }
  if (/ERR_MODULE_NOT_FOUND|Cannot find module/.test(out)) return "the mutated file could not be resolved";
  if (/SyntaxError/.test(out)) return "the mutated file did not parse";
  return "the suite failed without naming a case";
}

function runSuite(app, limitMs = timeoutMs) {
  return new Promise((done) => {
    const child = spawn(
      process.execPath,
      ["--test", "--test-reporter=tap", "tests/contracts/__conformance-only.test.ts"],
      { cwd: app, stdio: ["ignore", "pipe", "pipe"] },
    );
    let out = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, limitMs);
    const collect = (chunk) => {
      out += chunk;
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
    child.on("close", (code) => {
      clearTimeout(timer);
      done({ code, failed: failingCases(out), out, timedOut });
    });
  });
}

function applyMutant(app, mutant) {
  const target = join(app, mutant.file);
  const pristine = readFileSync(join(appRoot, mutant.file), "utf8");
  const occurrences = pristine.split(mutant.find).length - 1;
  if (occurrences === 0) return { ok: false, why: "its `find` text is not in the source any more" };
  if (occurrences > 1) {
    return { ok: false, why: `its \`find\` text appears ${occurrences} times, so the edit is ambiguous` };
  }
  writeFileSync(target, pristine.replace(mutant.find, mutant.replace));
  return { ok: true, restore: () => writeFileSync(target, pristine) };
}

/** Classifies one finished run. The only path to "killed" is a named, declared case failing. */
function classify(mutant, run) {
  if (run.timedOut) return { outcome: "runner-error", why: "the suite did not finish in time" };
  if (!/^# tests \d+/m.test(run.out)) {
    return { outcome: "runner-error", why: "the suite produced no TAP summary" };
  }
  // No named case failed, yet the process failed: the suite did not really run, so this mutant
  // tells us nothing either way.
  if (run.code !== 0 && run.failed.length === 0) {
    return { outcome: "runner-error", why: loadFailureReason(run.out) };
  }
  const declared = mutant.cases ?? [];
  if (declared.length === 0) {
    return { outcome: "runner-error", why: "the mutant declares no `cases`, so a kill cannot be attributed" };
  }
  if (run.code === 0) return { outcome: "survived", why: "the suite passed" };
  const matched = run.failed.filter((name) => declared.includes(name));
  if (matched.length === 0) {
    return {
      outcome: "survived",
      why: `the suite failed, but not in a declared case (failed: ${run.failed.join("; ") || "none named"})`,
    };
  }
  return { outcome: "killed", by: matched[0] };
}

// ---------------------------------------------------------------------------
// Self-tests: the runner's own claims about what it can and cannot detect (R40)
// ---------------------------------------------------------------------------

/** A case every self-test mutant can point at, so only the outcome under test varies. */
const SELF_CASE = "a new organization starts at zero with no ledger history";
const SCALE = "export const MONEY_SCALE = 8;";
const TENANT = "    const org = orgs.get(session.orgId);";

const SELF_TESTS = [
  {
    name: "a syntax error is a runner error, not a kill",
    mutant: { id: "SELF-SYNTAX", file: "lib/contracts/money.ts", find: SCALE, replace: "export const MONEY_SCALE = ;", cases: [SELF_CASE] },
    expect: "runner-error",
  },
  {
    name: "a module that throws on load is a runner error, not a kill",
    mutant: {
      id: "SELF-LOAD-THROW",
      file: "lib/contracts/money.ts",
      find: SCALE,
      replace: `${SCALE}\nthrow new Error("self-test load failure");`,
      cases: [SELF_CASE],
    },
    expect: "runner-error",
  },
  {
    name: "a construct type stripping rejects is a runner error, not a kill",
    mutant: {
      id: "SELF-ENUM",
      file: "lib/contracts/money.ts",
      find: SCALE,
      replace: `${SCALE}\nenum SelfTest {\n  A = 1,\n}\nexport const selfTestValue = SelfTest.A;`,
      cases: [SELF_CASE],
    },
    expect: "runner-error",
  },
  {
    name: "a hang is a runner error, not a kill",
    mutant: {
      id: "SELF-HANG",
      file: "lib/contracts/money.ts",
      find: SCALE,
      // `setInterval` keeps the loop alive, so this is a real hang rather than an "await never
      // settled" exit — the timeout path is what this self-test is for.
      replace: `${SCALE}\nsetInterval(() => {}, 1000);\nawait new Promise(() => {});`,
      cases: [SELF_CASE],
    },
    expect: "runner-error",
    timeoutMs: 4000,
  },
  {
    name: "a no-op edit survives",
    mutant: { id: "SELF-NOOP", file: "lib/contracts/money.ts", find: SCALE, replace: `${SCALE} // self-test no-op`, cases: [SELF_CASE] },
    expect: "survived",
  },
  {
    name: "a `find` that no longer matches is stale",
    mutant: { id: "SELF-STALE", file: "lib/contracts/money.ts", find: "export const MONEY_SCALE = 8888;", replace: SCALE, cases: [SELF_CASE] },
    expect: "stale",
  },
  {
    name: "a real defect attributed to the wrong case survives",
    mutant: {
      id: "SELF-WRONG-CASE",
      file: "lib/contracts/fake-services.ts",
      find: TENANT,
      replace: "    const org = [...orgs.values()][0];",
      cases: ["a case the suite does not have"],
    },
    expect: "survived",
  },
  {
    name: "a genuine defect in its declared case is killed",
    mutant: {
      id: "SELF-REAL-KILL",
      file: "lib/contracts/fake-services.ts",
      find: TENANT,
      replace: "    const org = [...orgs.values()][0];",
      cases: [SELF_CASE],
    },
    expect: "killed",
  },
  {
    name: "a mutant with no declared cases is a runner error",
    mutant: { id: "SELF-NO-CASES", file: "lib/contracts/fake-services.ts", find: TENANT, replace: "    const org = [...orgs.values()][0];", cases: [] },
    expect: "runner-error",
  },
];

async function runSelfTests() {
  const worker = prepareCopy();
  let failures = 0;
  try {
    console.log("runner self-tests — R40: the runner must not be able to report a false kill\n");
    for (const check of SELF_TESTS) {
      const applied = applyMutant(worker.app, check.mutant);
      let outcome;
      let why = "";
      if (!applied.ok) {
        outcome = "stale";
        why = applied.why;
      } else {
        const run = await runSuite(worker.app, check.timeoutMs ?? timeoutMs);
        applied.restore();
        const verdict = classify(check.mutant, run);
        outcome = verdict.outcome;
        why = verdict.why ?? verdict.by ?? "";
      }
      const ok = outcome === check.expect;
      if (!ok) failures += 1;
      console.log(`${ok ? "ok  " : "FAIL"} ${check.name}`);
      console.log(`       expected ${check.expect}, got ${outcome}${why ? ` — ${why}` : ""}`);
    }
  } finally {
    rmSync(worker.root, { recursive: true, force: true });
  }
  console.log(`\n${SELF_TESTS.length} self-tests, ${SELF_TESTS.length - failures} passed, ${failures} failed`);
  return failures === 0;
}

// ---------------------------------------------------------------------------
// The catalogue run
// ---------------------------------------------------------------------------

async function runCatalogue() {
  const catalogue = JSON.parse(readFileSync(join(here, "mutants.json"), "utf8"));
  const only = new Set((flag("--only", "") ?? "").split(",").filter((id) => id !== ""));
  const mutants = catalogue.mutants.filter((mutant) => only.size === 0 || only.has(mutant.id));
  if (mutants.length === 0) {
    console.error("no mutants selected");
    return 2;
  }

  const started = Date.now();
  const workers = [prepareCopy()];
  try {
    const baseline = await runSuite(workers[0].app);
    if (baseline.code !== 0) {
      console.error("the conformance suite does not pass unmutated — fix that first:\n");
      console.error(baseline.out.split("\n").filter((line) => /not ok|Error/.test(line)).join("\n"));
      return 2;
    }
    const knownCases = new Set([...passingCases(baseline.out), ...failingCases(baseline.out)]);
    console.log(
      `baseline: ${knownCases.size} exported cases pass unmutated; ${mutants.length} mutants, ${jobs} at a time, ` +
        `${timeoutMs} ms each\n`,
    );
    for (let i = 1; i < jobs; i += 1) workers.push(prepareCopy());

    const misattributed = mutants.filter((mutant) =>
      (mutant.cases ?? []).some((name) => !knownCases.has(name)),
    );

    const results = [];
    let next = 0;
    await Promise.all(
      workers.map(async (worker) => {
        for (;;) {
          const index = next;
          next += 1;
          if (index >= mutants.length) return;
          const mutant = mutants[index];
          const applied = applyMutant(worker.app, mutant);
          if (!applied.ok) {
            results.push({ mutant, outcome: "stale", why: applied.why });
            continue;
          }
          const run = await runSuite(worker.app);
          applied.restore();
          results.push({ mutant, ...classify(mutant, run) });
        }
      }),
    );

    results.sort((a, b) => a.mutant.id.localeCompare(b.mutant.id));
    const counts = { killed: 0, survived: 0, stale: 0, "runner-error": 0 };
    for (const result of results) {
      counts[result.outcome] += 1;
      const label = result.outcome === "killed" ? "killed  " : result.outcome.toUpperCase().padEnd(8);
      const detail =
        result.outcome === "killed" ? result.by : `${result.mutant.note ?? ""}${result.why ? ` — ${result.why}` : ""}`;
      console.log(`${label} ${result.mutant.id.padEnd(16)} ${detail}`);
    }

    const seconds = ((Date.now() - started) / 1000).toFixed(1);
    console.log(
      `\n${results.length} mutants: ${counts.killed} killed by a named declared case, ${counts.survived} survived, ` +
        `${counts.stale} stale, ${counts["runner-error"]} runner errors, ${seconds}s`,
    );

    let exitCode = 0;
    const report = (outcome, heading) => {
      const rows = results.filter((entry) => entry.outcome === outcome);
      if (rows.length === 0) return;
      console.error(`\n${heading}`);
      for (const row of rows) console.error(`  ${row.mutant.id}: ${row.mutant.note ?? ""} [${row.why ?? ""}]`);
      exitCode = 1;
    };
    report(
      "survived",
      "A surviving mutant means the exported suite names an invariant it cannot enforce, so track C could\n" +
        "pass the suite with that defect. Add or strengthen a case, or delete the claim:",
    );
    report(
      "runner-error",
      "A runner error means the mutant told us nothing: the copy did not run as a suite, so the outcome is\n" +
        "neither a kill nor a survival. Fix the mutant (or the runner):",
    );
    report("stale", "A stale mutant no longer matches the source, so it tests nothing. Update mutants.json:");
    if (misattributed.length > 0) {
      console.error("\nThese mutants name a case the suite does not have, so a kill could not be attributed:");
      for (const mutant of misattributed) {
        console.error(`  ${mutant.id}: ${mutant.cases.filter((name) => !knownCases.has(name)).join("; ")}`);
      }
      exitCode = 1;
    }
    return exitCode;
  } finally {
    if (keep) console.log(`\ncopies kept at ${workers.map((worker) => worker.root).join(", ")}`);
    else for (const worker of workers) rmSync(worker.root, { recursive: true, force: true });
  }
}

const code = selfTest ? ((await runSelfTests()) ? 0 : 1) : await runCatalogue();
process.exit(code);
