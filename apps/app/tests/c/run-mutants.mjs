#!/usr/bin/env node
// R32 for track C1: the read half's own mutation run.
//
// Same runner pattern as `tests/contracts/run-mutants.mjs` (F2's), pointed at C1's adapter instead of
// the fake, because a mutant has to be judged by the cases C1 actually runs: the EXPORTED console
// conformance suite plus this track's own tests. It differs from F2's runner in one way, and the
// difference is the point: the exported suite does not pass in full against C1 (the mutations are C3's
// and trace content is C2's), so the baseline requirement is not "exit 0" but **every declared case
// passes unmutated**. A mutant declaring a case that does not pass at baseline is a runner error, so
// the weaker baseline cannot hide a false kill.
//
// A kill requires a case the mutant declares to fail *on an assertion*. Everything else — a copy that
// does not load, parse or survive type stripping, a hang, a failure naming no case, a stale or
// ambiguous `find`, "every case failed" — is a runner error that fails the run.
//
// Usage: node tests/c/run-mutants.mjs [--only ID,ID] [--jobs N] [--timeout MS] [--keep]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const only = new Set((flag("--only", "") || "").split(",").filter(Boolean));
const jobs = Math.max(1, Number(flag("--jobs", "4")) || 4);
const timeoutMs = Math.max(1000, Number(flag("--timeout", "120000")) || 120000);
const keep = args.includes("--keep");

/** What C1 runs: the exported conformance functions against the real services, and this track's tests. */
const SUITE = [
  "tests/c/conformance/console-services.conformance.ts",
  "tests/c/query-boundary.test.ts",
  "tests/c/cursor.test.ts",
  "tests/c/read-services.test.ts",
  "tests/c/projection.test.ts",
  "tests/c/credits.test.ts",
  "tests/c/client-boundary.test.ts",
  // C0: the consumer context and read port (the real-PostgREST twin skips without its stack).
  "tests/c/consumer.test.ts",
  // C3A: the trusted consumer actions (the real-PostgREST twin skips without its stack).
  "tests/c/actions.test.ts",
  // S1-fix B2: the production preview gate is a module-level constant, so its case lives with the module.
  "app/(console)/usage/preview-context.test.ts",
  // E3A F-2: the provider-route guard and its wiring in /traces, /dedicated, /teams.
  "tests/c/provider-routes.test.ts",
];

function prepareCopy() {
  const root = mkdtempSync(join(tmpdir(), "c1-mutants-"));
  const app = join(root, "app");
  cpSync(appRoot, app, {
    recursive: true,
    dereference: false,
    filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
  });
  symlinkSync(join(appRoot, "node_modules"), join(app, "node_modules"), "dir");
  return { root, app };
}

/**
 * The failing cases, with *how* each failed. A kill has to be an assertion: a case that fell over with
 * a TypeError says the copy is broken, not that the suite checks anything. Cases appear at indent 0
 * (this track's top-level tests) and at indent 4 (inside the conformance `describe`).
 */
function failingCases(out) {
  const cases = [];
  const pattern = /^( *)not ok \d+ - (.*)$/gm;
  for (let match = pattern.exec(out); match !== null; match = pattern.exec(out)) {
    const rest = out.slice(match.index + match[0].length);
    // The block ends at `...` indented exactly two past the `not ok` line. Matching any `...` line
    // truncated the diagnostic in the middle of a long diff — node elides identical lines with one —
    // which lost the `code: 'ERR_ASSERTION'` that decides whether this was an assertion or a crash.
    const terminator = new RegExp(`^ {${match[1].length + 2}}\\.\\.\\.$`, "m");
    const end = rest.search(terminator);
    const diagnostic = end === -1 ? rest : rest.slice(0, end);
    const assertion = /code: 'ERR_ASSERTION'/.test(diagnostic);
    const wrapped = /threw instead of returning a Result/.test(diagnostic);
    const error = /^ *name: '(\w+)'/m.exec(diagnostic);
    const name = match[2].trim();
    cases.push({
      name,
      how: assertion && !wrapped ? "assertion" : "error",
      error: wrapped ? "an exception surfaced by the suite's own wrapper" : (error?.[1] ?? "unknown"),
      // A test *file* reported as failing means the copy did not load — a syntax error, a rejected
      // construct, a module that throws on import. That is a runner error however the rest reads, and
      // it used to be classified as "survived" because no declared case was named.
      isFile: /\.(ts|mjs|tsx)$/.test(name),
      // guarded() turns any escaped exception into `internal_error`. A mutant that merely made some
      // read throw would then "kill" a case that asserts a successful read — which says nothing about
      // the invariant the case names, so it has to be declared. Keyed on the *code* an ok-expectation
      // reported, not on the guard's prose, so rewording the message cannot change a classification.
      viaGuard: /(?:expected success, got|failed:) internal_error/.test(diagnostic),
    });
  }
  return cases;
}

function passingCases(out) {
  return [...out.matchAll(/^ *ok \d+ - (.*)$/gm)].map((match) => match[1].trim());
}

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
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...SUITE], {
      cwd: app,
      stdio: ["ignore", "pipe", "pipe"],
      env,
    });
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
      done({ code, failed: failingCases(out), passed: passingCases(out), out, timedOut });
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

function classify(mutant, run, baselinePassing) {
  if (run.timedOut) return { outcome: "runner-error", why: "the suite did not finish in time" };
  if (!/^# tests \d+/m.test(run.out)) return { outcome: "runner-error", why: "the suite produced no TAP summary" };
  const declared = mutant.cases ?? [];
  if (declared.length === 0) {
    return { outcome: "runner-error", why: "the mutant declares no `cases`, so a kill cannot be attributed" };
  }
  const notAtBaseline = declared.filter((name) => !baselinePassing.has(name));
  if (notAtBaseline.length > 0) {
    return { outcome: "runner-error", why: `these declared cases do not pass unmutated: ${notAtBaseline.join("; ")}` };
  }
  const fileFailures = run.failed.filter((entry) => entry.isFile);
  if (fileFailures.length > 0) {
    return {
      outcome: "runner-error",
      why: `${fileFailures.map((entry) => entry.name).join(", ")} did not run as a suite — ${loadFailureReason(run.out)}`,
    };
  }
  if (run.failed.length === 0) {
    // Nothing failed at all: the mutant changed no observable behaviour the suite checks.
    return { outcome: "survived", why: "the suite passed unchanged" };
  }
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
      why: `the suite failed, but not in a declared case (failed: ${run.failed.map((entry) => entry.name).join("; ")})`,
    };
  }
  const byAssertion = matched.filter((entry) => entry.how === "assertion");
  if (byAssertion.length === 0) {
    return {
      outcome: "runner-error",
      why: `the declared case failed by exception (${matched[0].error}), not by assertion`,
    };
  }
  const genuine = byAssertion.filter((entry) => !entry.viaGuard);
  if (genuine.length === 0 && mutant.kills_by !== "guarded") {
    return {
      outcome: "runner-error",
      why:
        "the declared case failed only because the boundary guard turned a thrown error into " +
        'internal_error — if that IS the defect, declare kills_by: "guarded"',
    };
  }
  if (genuine.length > 0 && mutant.kills_by === "guarded") {
    return {
      outcome: "runner-error",
      why: 'the mutant declares kills_by: "guarded" but its case fails on the invariant itself',
    };
  }
  const by = (genuine[0] ?? byAssertion[0]).name;
  return { outcome: "killed", by, collateral: run.failed.length - matched.length };
}

/**
 * The runner's own claims (R40's discipline, in the small): the three classifications a reviewer
 * asked for, checked against edits whose outcome is known. Run with `--self-test`.
 */
const SELF_TESTS = [
  {
    name: "a syntax error is a runner error, not a survival",
    mutant: {
      id: "SELF-SYNTAX",
      file: "lib/services/cursor.ts",
      find: "const MAX_CURSOR_CHARS = 512;",
      replace: "const MAX_CURSOR_CHARS = ;",
      cases: ["rejects a cursor it did not issue for this query"],
    },
    expect: "runner-error",
  },
  {
    name: "a no-op edit survives",
    mutant: {
      id: "SELF-NOOP",
      file: "lib/services/cursor.ts",
      find: "const MAX_CURSOR_CHARS = 512;",
      replace: "const MAX_CURSOR_CHARS = 512; // self-test no-op",
      cases: ["rejects a cursor it did not issue for this query"],
    },
    expect: "survived",
  },
  {
    name: "a kill that only the boundary guard produced is a runner error unless declared",
    mutant: {
      id: "SELF-GUARD",
      file: "lib/services/console.ts",
      // Nothing about the wallet identity: the read simply throws, and `guarded()` reports
      // `internal_error`, which would otherwise read as a kill of whatever case asserted a read.
      find: "async function walletFor(orgId: string): Promise<WalletBalance | null> {",
      replace:
        "async function walletFor(orgId: string): Promise<WalletBalance | null> {\n    throw new Error(\"self-test\");",
      cases: ["balance is the ledger total minus reservations, and holds reduce what is available"],
    },
    expect: "runner-error",
  },
  {
    name: "a stale find is stale",
    mutant: {
      id: "SELF-STALE",
      file: "lib/services/cursor.ts",
      find: "const MAX_CURSOR_CHARS = 999999;",
      replace: "const MAX_CURSOR_CHARS = 512;",
      cases: ["rejects a cursor it did not issue for this query"],
    },
    expect: "stale",
  },
];

async function runSelfTests() {
  const worker = prepareCopy();
  let failures = 0;
  try {
    const baseline = await runSuite(worker.app);
    const baselinePassing = new Set(passingCases(baseline.out));
    for (const { name, mutant, expect } of SELF_TESTS) {
      const applied = applyMutant(worker.app, mutant);
      let outcome;
      if (!applied.ok) {
        outcome = { outcome: "stale", why: applied.why };
      } else {
        const run = await runSuite(worker.app, 60000);
        applied.restore();
        outcome = classify(mutant, run, baselinePassing);
      }
      const ok = outcome.outcome === expect;
      if (!ok) failures += 1;
      console.log(`${ok ? "ok  " : "FAIL"} ${name} — got ${outcome.outcome}${outcome.why ? `: ${outcome.why}` : ""}`);
    }
  } finally {
    rmSync(worker.root, { recursive: true, force: true });
  }
  console.log(`\n${SELF_TESTS.length} self-tests, ${failures} failed`);
  return failures === 0;
}

if (args.includes("--self-test")) {
  process.exit((await runSelfTests()) ? 0 : 1);
}

const catalogue = JSON.parse(readFileSync(join(here, "mutants.json"), "utf8"));
const mutants = catalogue.mutants.filter((mutant) => only.size === 0 || only.has(mutant.id));
if (mutants.length === 0) {
  console.error("no mutants selected");
  process.exit(2);
}

const started = Date.now();
const workers = [prepareCopy()];
let exitCode = 0;
try {
  const baseline = await runSuite(workers[0].app);
  const baselinePassing = new Set(passingCases(baseline.out));
  const baselineFailing = failingCases(baseline.out).map((entry) => entry.name);
  if (baselinePassing.size === 0) {
    console.error(`the suite produced no passing case unmutated:\n${baseline.out.slice(0, 4000)}`);
    process.exit(2);
  }
  console.log(
    `baseline: ${baselinePassing.size} cases pass unmutated, ${baselineFailing.length} fail ` +
      `(C2/C3 operations this task does not implement); ${mutants.length} mutants, ${jobs} at a time\n`,
  );
  for (let i = 1; i < jobs; i += 1) workers.push(prepareCopy());

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
        results.push({ mutant, ...classify(mutant, run, baselinePassing) });
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
        ? `${result.by}${result.collateral > 0 ? ` [+${result.collateral} collateral]` : ""}`
        : `${result.mutant.note ?? ""}${result.why ? ` — ${result.why}` : ""}`;
    console.log(`${label} ${result.mutant.id.padEnd(14)} ${detail}`);
  }
  console.log(
    `\n${results.length} mutants: ${counts.killed} killed by a named declared case, ${counts.survived} survived, ` +
      `${counts.stale} stale, ${counts["runner-error"]} runner errors, ${((Date.now() - started) / 1000).toFixed(1)}s`,
  );
  for (const [outcome, heading] of [
    ["survived", "A surviving mutant means a case claims an invariant it cannot enforce:"],
    ["runner-error", "A runner error means the mutant told us nothing:"],
    ["stale", "A stale mutant no longer matches the source, so it tests nothing:"],
  ]) {
    const rows = results.filter((entry) => entry.outcome === outcome);
    if (rows.length === 0) continue;
    console.error(`\n${heading}`);
    for (const row of rows) console.error(`  ${row.mutant.id}: ${row.mutant.note ?? ""} [${row.why ?? ""}]`);
    exitCode = 1;
  }
} finally {
  if (keep) console.log(`\ncopies kept at ${workers.map((worker) => worker.root).join(", ")}`);
  else for (const worker of workers) rmSync(worker.root, { recursive: true, force: true });
}
process.exit(exitCode);
