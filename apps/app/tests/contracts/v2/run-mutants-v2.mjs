#!/usr/bin/env node
// R32/R40 for the console half of contracts v2: the v2 suites' own test.
//
// Every invariant a v2 test names must be *killable*: if a single-edit change to
// `lib/contracts/v2/` can break the invariant and the suite still passes, the suite is
// claiming something it does not check. This applies each mutant of `mutants-v2.json` to a
// temporary copy and requires it to be killed by a NAMED test.
//
// What counts as a kill (R40: the runner must not be able to report a false one):
//
//   - at least one `not ok` line naming a test, and
//   - every such name is one of the mutant's declared `cases`.
//
// A non-zero exit on its own is NOT a kill. A syntax error, a module that throws on load, a
// construct Node's type stripping rejects, a hang, or a suite that fails without naming a
// test are all RUNNER-ERROR: the mutant told us nothing, and the run fails.
//
// The copy mirrors the repository layout — `<tmp>/apps/app/lib/contracts`,
// `<tmp>/apps/app/tests/contracts/v2`, `<tmp>/apps/infrx-api/infrx/contracts/fixtures/v2` —
// because the v2 suites read the Python fixture directory by relative path rather than
// keeping copies. Nothing is written inside the worktree, and no node_modules is needed:
// the v2 suites import only node builtins and relative files.
//
// This is a separate runner from `tests/contracts/run-mutants.mjs` only because that one
// hard-codes its entry point to the v1 console-services conformance module. The wire-in
// phase should give that runner an `--entry` and delete this file.
// ponytail: second runner, deleted when run-mutants.mjs takes an entry point.
//
// Usage: node tests/contracts/v2/run-mutants-v2.mjs [--only ID,ID] [--timeout MS] [--keep]
//        node tests/contracts/v2/run-mutants-v2.mjs --self-test
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../../..");
const repoApps = resolve(appRoot, "..");

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const timeoutMs = Math.max(1000, Number(flag("--timeout", "120000")) || 120000);
const keep = args.includes("--keep");
const selfTest = args.includes("--self-test");
const only = flag("--only", null);

const declared = JSON.parse(readFileSync(join(here, "mutants-v2.json"), "utf8"));
const NOT_OK = /^not ok \d+ - (.+?)\s*$/;

/** Build a throwaway mirror of the three directories the v2 suites touch. */
function stage() {
  const root = mkdtempSync(join(tmpdir(), "infrx-v2-mutant-"));
  mkdirSync(join(root, "apps/app/tests/contracts"), { recursive: true });
  mkdirSync(join(root, "apps/infrx-api/infrx/contracts"), { recursive: true });
  cpSync(join(appRoot, "lib/contracts"), join(root, "apps/app/lib/contracts"), {
    recursive: true,
  });
  cpSync(join(here), join(root, "apps/app/tests/contracts/v2"), { recursive: true });
  cpSync(
    join(repoApps, "infrx-api/infrx/contracts/fixtures/v2"),
    join(root, "apps/infrx-api/infrx/contracts/fixtures/v2"),
    { recursive: true },
  );
  return root;
}

function apply(root, mutant) {
  const target = join(root, "apps/app", mutant.file);
  const source = readFileSync(target, "utf8");
  const hits = source.split(mutant.find).length - 1;
  if (hits === 0) return `anchor not found in ${mutant.file}: ${mutant.find.slice(0, 60)}`;
  if (hits > 1) return `anchor is not unique in ${mutant.file} (${hits} occurrences)`;
  writeFileSync(target, source.replace(mutant.find, mutant.replace));
  return null;
}

function run(root) {
  const suiteDir = join(root, "apps/app/tests/contracts/v2");
  const files = readdirSync(suiteDir)
    .filter((name) => name.endsWith(".test.ts"))
    .map((name) => join(suiteDir, name));
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", ...files], {
      cwd: join(root, "apps/app"),
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
      done({ code, out, timedOut });
    });
  });
}

/** killed | survived | runner-error, and never anything else. */
async function judge(mutant) {
  if (!Array.isArray(mutant.cases) || mutant.cases.length === 0) {
    return { outcome: "runner-error", detail: "declares no case" };
  }
  const root = stage();
  try {
    const bad = apply(root, mutant);
    if (bad !== null) return { outcome: "runner-error", detail: bad };
    const { code, out, timedOut } = await run(root);
    if (timedOut) return { outcome: "runner-error", detail: "timed out" };
    const failed = out
      .split("\n")
      .map((line) => line.match(NOT_OK))
      .filter(Boolean)
      .map((match) => match[1]);
    if (failed.length === 0) {
      return code === 0
        ? { outcome: "survived", detail: "the suite passed with the defect in place" }
        : { outcome: "runner-error", detail: `exit ${code} with no failing test named` };
    }
    const stray = failed.filter((name) => !mutant.cases.includes(name));
    if (stray.length > 0) {
      return { outcome: "runner-error", detail: `failures outside the named cases: ${stray[0]}` };
    }
    return { outcome: "killed", detail: `${failed.length} named test(s) failed` };
  } finally {
    if (!keep) rmSync(root, { recursive: true, force: true });
    else console.log(`  kept ${root}`);
  }
}

// The runner's own honesty: each outcome is produced deliberately, so a runner that
// counted a syntax error as a kill could not pass this.
const SELF = [
  {
    id: "SELF-noop",
    expect: "survived",
    note: "an edit that changes nothing is a survivor",
    file: "lib/contracts/v2/types.ts",
    find: "export const WALLET_KINDS",
    replace: "// a comment changes no behaviour\nexport const WALLET_KINDS",
    cases: ["available credit is recomputed, not trusted"],
  },
  {
    id: "SELF-anchor",
    expect: "runner-error",
    note: "a missing anchor is a runner error, never a kill",
    file: "lib/contracts/v2/types.ts",
    find: "this text is not in the module",
    replace: "nor is this",
    cases: ["available credit is recomputed, not trusted"],
  },
  {
    id: "SELF-syntax",
    expect: "runner-error",
    note: "a syntax error is a runner error, never a kill",
    file: "lib/contracts/v2/types.ts",
    find: "export const WALLET_KINDS",
    replace: "export const )))) WALLET_KINDS",
    cases: ["available credit is recomputed, not trusted"],
  },
  {
    id: "SELF-wrong-case",
    expect: "runner-error",
    note: "a real defect that fails a test the mutant did not name is not a kill",
    file: "lib/contracts/v2/types.ts",
    find: "  return subCredit(parseCredit(balance.ledger_total), parseCredit(balance.reserved_total));",
    replace: "  return parseCredit(balance.ledger_total);",
    cases: ["a grant is checked current, and every dimension must match"],
  },
  {
    id: "SELF-no-case",
    expect: "runner-error",
    note: "a mutant naming no case proves nothing",
    file: "lib/contracts/v2/types.ts",
    find: "export const WALLET_KINDS",
    replace: "export const WALLET_KINDS",
    cases: [],
  },
];

async function main() {
  const list = selfTest
    ? SELF
    : declared.mutants.filter((m) => only === null || only.split(",").includes(m.id));
  if (list.length === 0) {
    console.error("no mutants selected");
    return 1;
  }
  let failures = 0;
  for (const mutant of list) {
    const result = await judge(mutant);
    const expected = selfTest ? mutant.expect : "killed";
    const ok = result.outcome === expected;
    if (!ok) failures += 1;
    console.log(
      `${ok ? "ok  " : "FAIL"} ${mutant.id.padEnd(14)} ${result.outcome.padEnd(12)} ${mutant.note ?? ""}` +
        (ok ? "" : `\n       expected ${expected}: ${result.detail}`),
    );
  }
  console.log(
    `\n${list.length - failures}/${list.length} ${selfTest ? "self-tests as expected" : "mutants killed"}`,
  );
  return failures === 0 ? 0 : 1;
}

process.exit(await main());
