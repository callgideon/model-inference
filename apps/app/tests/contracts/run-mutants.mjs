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
// One runner, several entry points (F2P wire-in item 11). A mutant's `entry` names the suite that
// must kill it: `conformance` (the default) is the exported console-services conformance, as
// above; `v2` is the contracts-v2 console suites (`tests/contracts/v2/*.test.ts`), which read the
// Python fixture base by relative path, so every copy also carries
// `apps/infrx-api/infrx/contracts/fixtures/v2` beside the app; `fixtures` is the console fixture
// guards (`tests/contracts/fixtures.test.ts`). The kill rule is the same for
// every entry. `--entry NAME` runs one entry's mutants; the default runs all of them.
//
// Usage: node tests/contracts/run-mutants.mjs [--entry NAME] [--only ID,ID] [--jobs N] [--timeout MS] [--keep]
//        node tests/contracts/run-mutants.mjs --self-test
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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
const onlyEntry = flag("--entry", null);

/**
 * The entry point each run executes: only the exported conformance functions, against the fake.
 * Written into the copy so a mutant is judged by what C would run and nothing else.
 */
const ENTRY = `import { runConsoleServicesConformance } from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";

runConsoleServicesConformance(() => {
  const services = createFakeConsoleServices();
  return { services, sessions: services.sessions, ids: services.ids, hasLegacyRows: services.hasLegacyRows };
}, "mutation target");
`;

/**
 * Where each entry's suite lives in a copy, and at what TAP depth its cases report: the
 * conformance cases are subtests of one suite (4 spaces), the v2 suites' tests are top level.
 */
const ENTRIES = {
  conformance: { files: () => ["tests/contracts/__conformance-only.test.ts"], indent: 4 },
  v2: {
    files: (app) =>
      readdirSync(join(app, "tests/contracts/v2"))
        .filter((name) => name.endsWith(".test.ts"))
        .sort()
        .map((name) => `tests/contracts/v2/${name}`),
    indent: 0,
  },
  // The console fixture guards (F2R-B NB-2): fixture *data* invariants, killed by editing the data.
  fixtures: { files: () => ["tests/contracts/fixtures.test.ts"], indent: 0 },
};

function entryOf(mutant) {
  const name = mutant.entry ?? "conformance";
  if (!(name in ENTRIES)) throw new Error(`mutant ${mutant.id}: unknown entry ${name}`);
  return name;
}

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
  // The v2 suites read the Python fixture base at ../../../../infrx-api/... from their own file.
  cpSync(
    resolve(appRoot, "../infrx-api/infrx/contracts/fixtures/v2"),
    join(root, "infrx-api/infrx/contracts/fixtures/v2"),
    { recursive: true },
  );
  return { root, app };
}

/**
 * The failing subtests, with *how* each failed. A kill has to be an assertion: a case that fell over
 * with a TypeError tells us the copy is broken, not that the suite checks anything. The suite's own
 * `settle()` wrapper reports a thrown error through `assert.fail`, so its message is recognised too —
 * it is a thrown error wearing an assertion's clothes.
 */
function failingCases(out, indent = 4) {
  const cases = [];
  const pad = " ".repeat(indent);
  const yaml = " ".repeat(indent + 2);
  const pattern = new RegExp(`^${pad}not ok \\d+ - (.*)$`, "gm");
  for (let match = pattern.exec(out); match !== null; match = pattern.exec(out)) {
    const rest = out.slice(match.index + match[0].length);
    const end = rest.search(new RegExp(`^${yaml}\\.\\.\\.$`, "m"));
    const diagnostic = end === -1 ? rest : rest.slice(0, end);
    const assertion = /code: 'ERR_ASSERTION'/.test(diagnostic);
    const wrapped = /threw instead of returning a Result/.test(diagnostic);
    const error = new RegExp(`^${yaml}name: '(\\w+)'`, "m").exec(diagnostic);
    cases.push({
      name: match[1].trim(),
      how: assertion && !wrapped ? "assertion" : "error",
      error: wrapped ? "an exception surfaced by the suite's own wrapper" : (error?.[1] ?? "unknown"),
    });
  }
  return cases;
}

function failingNames(out, indent = 4) {
  return failingCases(out, indent).map((entry) => entry.name);
}

function passingCases(out, indent = 4) {
  const pattern = new RegExp(`^${" ".repeat(indent)}ok \\d+ - (.*)$`, "gm");
  return [...out.matchAll(pattern)].map((match) => match[1].trim());
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

function runSuite(app, limitMs = timeoutMs, entry = "conformance") {
  const { files, indent } = ENTRIES[entry];
  return new Promise((done) => {
    const child = spawn(
      process.execPath,
      ["--test", "--test-reporter=tap", ...files(app)],
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
      done({
        code,
        failed: failingCases(out, indent),
        passed: passingCases(out, indent),
        indent,
        out,
        timedOut,
      });
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

/**
 * Classifies one finished run. The only path to "killed" is a declared case failing *on an
 * assertion* — or, where the mutant declares `kills_by: "throw"`, on the exception that is itself
 * the defect. Everything else is a runner error, because a mutant that makes the whole suite fall
 * over has not demonstrated that the suite checks anything.
 */
function classify(mutant, run) {
  if (run.timedOut) return { outcome: "runner-error", why: "the suite did not finish in time" };
  if (!/^# tests \d+/m.test(run.out)) {
    return { outcome: "runner-error", why: "the suite produced no TAP summary" };
  }
  if (run.code !== 0 && run.failed.length === 0) {
    return { outcome: "runner-error", why: loadFailureReason(run.out) };
  }
  const declared = mutant.cases ?? [];
  if (declared.length === 0) {
    return { outcome: "runner-error", why: "the mutant declares no `cases`, so a kill cannot be attributed" };
  }
  if (run.code === 0) return { outcome: "survived", why: "the suite passed" };

  // A mutant that breaks *everything* — a crash in the harness factory, say — has told us nothing
  // about any particular invariant, whatever its declared case says.
  if (run.passed.length === 0) {
    return {
      outcome: "runner-error",
      why: `every case failed (${run.failed.length}), so the copy is broken rather than the invariant caught`,
    };
  }

  const matched = run.failed.filter((entry) => declared.includes(entry.name));
  if (matched.length === 0) {
    return {
      outcome: "survived",
      why: `the suite failed, but not in a declared case (failed: ${failingNames(run.out, run.indent).join("; ") || "none named"})`,
    };
  }
  const collateral = run.failed.length - matched.length;
  const byAssertion = matched.filter((entry) => entry.how === "assertion");
  if (byAssertion.length === 0) {
    if (mutant.kills_by !== "throw") {
      return {
        outcome: "runner-error",
        why:
          `the declared case failed by exception (${matched[0].error}), not by assertion — ` +
          `if that exception IS the defect, declare kills_by: "throw"`,
      };
    }
    return { outcome: "killed", by: matched[0].name, collateral, how: "throw" };
  }
  if (mutant.kills_by === "throw") {
    return {
      outcome: "runner-error",
      why: "the mutant declares kills_by: \"throw\" but its case fails on an assertion, so the declaration is wrong",
    };
  }
  return { outcome: "killed", by: byAssertion[0].name, collateral, how: "assertion" };
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
    // Named for what it actually does: the declared case does not exist, so nothing can
    // run it and the defect survives. "attributed to the wrong case" described a
    // different check — a real case that cannot see the defect — which is what
    // SELF-NO-CASES and the Python `self_wrong_case` cover.
    name: "a real defect declared against a case the suite does not have survives",
    mutant: {
      id: "SELF-UNKNOWN-CASE",
      file: "lib/contracts/fake-services.ts",
      find: TENANT,
      replace: "    const org = [...orgs.values()][0];",
      cases: ["a case the suite does not have"],
    },
    expect: "survived",
  },
  {
    name: "a crash in the harness factory is a runner error, not a kill",
    mutant: {
      id: "SELF-FACTORY-CRASH",
      file: "lib/contracts/fake-services.ts",
      find: "export function createFakeConsoleServices(): FakeConsoleServices {",
      replace:
        "export function createFakeConsoleServices(): FakeConsoleServices {\n  (globalThis as never as { noSuchThing: { boom(): void } }).noSuchThing.boom();",
      cases: [SELF_CASE],
    },
    expect: "runner-error",
  },
  {
    name: "a declared case that fails by exception rather than assertion is a runner error",
    mutant: {
      id: "SELF-EXCEPTION-KILL",
      file: "lib/contracts/fake-services.ts",
      find: "      trace.feedback.push(entry);\n      trace.feedback_count = trace.feedback.length;",
      replace:
        "      (trace as never as { nope: { push(v: unknown): void } }).nope.push(entry);\n      trace.feedback_count = trace.feedback.length;",
      cases: ["feedback follows the R3 body, appears immediately, and has provenance the client cannot set"],
    },
    expect: "runner-error",
  },
  {
    // The edit the round-4 reviewer filed as V03 and which round 5 reported as a kill while it was
    // failing eight cases by TypeError. It is the **IDEM-05** variant, not V03 (V03 was the
    // fake-only "record committed with its effect" finding), and the label now says so: a
    // self-test named after the wrong mutant is a self-test nobody can check. It is a
    // *legitimate* kill now — the per-field idempotency case catches it on an assertion — and
    // this self-test exists to keep it that way: if the case that catches it ever weakens, the
    // classifier sees an exception again and refuses the kill.
    name: "the old IDEM-05 edit is killed on an assertion, not on an exception",
    mutant: {
      id: "SELF-IDEM-05-REPLICA",
      file: "lib/contracts/fake-services.ts",
      find: "    const value = apply();\n    remember(session, operation, key, payload, record(value));",
      replace: "    remember(session, operation, key, payload, record(undefined as never));\n    const value = apply();",
      cases: ["an idempotency key is scoped to its operation, its organization and its payload"],
    },
    expect: "killed",
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
  // The v2 entry (item 11): the same classification over the v2 suites' top-level tests.
  {
    name: "a no-op edit survives on the v2 entry",
    mutant: {
      id: "SELF-V2-NOOP",
      entry: "v2",
      file: "lib/contracts/v2/types.ts",
      find: "export const WALLET_KINDS",
      replace: "// a comment changes no behaviour\nexport const WALLET_KINDS",
      cases: ["available credit is recomputed, not trusted"],
    },
    expect: "survived",
  },
  {
    name: "a genuine v2 defect is killed by its declared v2 test",
    mutant: {
      id: "SELF-V2-REAL-KILL",
      entry: "v2",
      file: "lib/contracts/v2/types.ts",
      find: "  return subCredit(parseCredit(balance.ledger_total), parseCredit(balance.reserved_total));",
      replace: "  return parseCredit(balance.ledger_total);",
      cases: ["available credit is recomputed, not trusted"],
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
        const run = await runSuite(worker.app, check.timeoutMs ?? timeoutMs, entryOf(check.mutant));
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
  const mutants = catalogue.mutants.filter(
    (mutant) =>
      (only.size === 0 || only.has(mutant.id)) && (onlyEntry === null || entryOf(mutant) === onlyEntry),
  );
  if (mutants.length === 0) {
    console.error("no mutants selected");
    return 2;
  }

  const started = Date.now();
  const workers = [prepareCopy()];
  try {
    // One pristine baseline per entry the selection touches: every case a mutant may name must
    // pass unmutated, or its "kill" would be the suite's own failure.
    const knownByEntry = {};
    for (const entry of [...new Set(mutants.map(entryOf))].sort()) {
      const baseline = await runSuite(workers[0].app, timeoutMs, entry);
      if (baseline.code !== 0) {
        console.error(`the ${entry} suite does not pass unmutated — fix that first:\n`);
        console.error(baseline.out.split("\n").filter((line) => /not ok|Error/.test(line)).join("\n"));
        return 2;
      }
      knownByEntry[entry] = new Set([...baseline.passed, ...failingNames(baseline.out, baseline.indent)]);
      console.log(`baseline (${entry}): ${knownByEntry[entry].size} cases pass unmutated`);
    }
    console.log(`${mutants.length} mutants, ${jobs} at a time, ${timeoutMs} ms each\n`);
    for (let i = 1; i < jobs; i += 1) workers.push(prepareCopy());

    const knownFor = (mutant) => knownByEntry[entryOf(mutant)];
    const misattributed = mutants.filter((mutant) =>
      (mutant.cases ?? []).some((name) => !knownFor(mutant).has(name)),
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
          const run = await runSuite(worker.app, timeoutMs, entryOf(mutant));
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
        result.outcome === "killed"
          ? `${result.by}${result.how === "throw" ? " (by the exception it declares)" : ""}` +
            `${result.collateral > 0 ? ` [+${result.collateral} collateral]` : ""}`
          : `${result.mutant.note ?? ""}${result.why ? ` — ${result.why}` : ""}`;
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
        console.error(`  ${mutant.id}: ${mutant.cases.filter((name) => !knownFor(mutant).has(name)).join("; ")}`);
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
