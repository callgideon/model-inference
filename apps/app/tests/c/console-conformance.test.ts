// node --test "tests/**/*.test.ts"
//
// The exported conformance suite, run against C1's real services in a child process.
//
// Why a child process: `runConsoleServicesConformance` covers the whole `ConsoleServices` interface,
// and C1 implements the read half — the mutations are C3's and trace content is C2's. Registering the
// suite directly here would make `pnpm test` red for work this task does not own, and deleting the
// unowned cases would hide them. So the suite runs as a subprocess and this test asserts two things:
//
//   1. every case C1 owns passes, named explicitly — a read regression fails `pnpm test`;
//   2. every failing case fails *because an operation is not implemented yet*, and says whose it is.
//
// (2) is what keeps the report honest: a case cannot quietly fail for a tenant-isolation or
// pagination defect and be filed as "C3's problem", because the failure message would not name an
// unimplemented operation. The declared list is a subset check, so C3 landing its writes turns
// failures into passes without this file having to change.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");
const entry = "tests/c/conformance/console-services.conformance.ts";

/** The cases C1's read half is responsible for. Each one must pass. */
const OWNED_CASES = [
  "usage pages walk every row exactly once, newest first",
  "ledger and trace pages walk every row exactly once",
  "rejects a limit above the hard bound and a limit that is not a positive integer",
  "rejects a cursor it did not issue for this query",
  "balance is the ledger total minus reservations, and holds reduce what is available",
  "a new organization starts at zero with no ledger history",
  "identifiers from another organization are not_found, in both directions",
  "a filter or cursor from another organization never widens the tenant",
  "a filter outside its vocabulary is invalid_request, not an empty page",
  "a member can read but cannot mutate settings or keys",
  "a non-operator can neither grant nor see other organizations",
  "the HTTP status table is the one 08 §3 freezes, code for code",
  "judge runs separate estimates, limited evaluations and held budgets",
  "no operation accepts a field the caller invented",
  "a filter filters, and an absent filter does not",
  "the operator list pages like every other list",
];

/**
 * The marker every unimplemented operation returns, with the task that owns it — either quoted in
 * the message, or shown by the suite as an `internal_error` code where it expected the refusal the
 * write's own body validation will produce (`invalid_request` for a bad reason or retention). Both
 * forms are distinguishable from the defects this task must not have: those fail on `not_found`,
 * `invalid_cursor`, `forbidden`, an order or a row-set comparison, none of which match here.
 */
const NOT_IMPLEMENTED = /is not implemented in this service yet \((C2|C3)[^)]*\)|\+ 'internal_error'/;

// `NODE_TEST_CONTEXT` is set inside a test process; left in the child's environment, Node refuses to
// run files ("run() is being called recursively") and the subprocess reports nothing at all.
const childEnv = { ...process.env };
delete childEnv.NODE_TEST_CONTEXT;

const run = spawnSync(process.execPath, ["--test", "--test-reporter=tap", entry], {
  cwd: appRoot,
  encoding: "utf8",
  timeout: 300000,
  env: childEnv,
});
const output = `${run.stdout ?? ""}${run.stderr ?? ""}`;

function cases(marker: "ok" | "not ok"): string[] {
  const pattern = marker === "ok" ? /^ {4}ok \d+ - (.*)$/gm : /^ {4}not ok \d+ - (.*)$/gm;
  return [...output.matchAll(pattern)].map((match) => match[1].trim());
}

test("the exported conformance suite ran and produced a TAP summary", () => {
  assert.match(output, /^# tests \d+/m, `the suite produced no summary:\n${output.slice(0, 4000)}`);
  assert.ok(cases("ok").length + cases("not ok").length > 30, "the suite reported too few cases to be complete");
});

test("every conformance case C1 owns passes against the real services", () => {
  const passed = new Set(cases("ok"));
  const missing = OWNED_CASES.filter((name) => !passed.has(name));
  assert.deepEqual(missing, [], `these C1 cases did not pass:\n${missing.join("\n")}`);
});

test("every failing case fails only because an operation is not implemented in C1", () => {
  const failing = cases("not ok");
  // Each failing case's diagnostic block must name an unimplemented operation. A pagination, tenant
  // or projection defect would fail with a different message, and this assertion would catch it.
  const unexplained: string[] = [];
  for (const name of failing) {
    const start = output.indexOf(`- ${name}`);
    const block = output.slice(start, start + 2500);
    const end = block.search(/^ {4}(not )?ok \d+ - /m);
    if (!NOT_IMPLEMENTED.test(end === -1 ? block : block.slice(0, end))) unexplained.push(name);
  }
  assert.deepEqual(
    unexplained,
    [],
    `these cases failed for a reason other than an unimplemented C2/C3 operation:\n${unexplained.join("\n")}`,
  );
});
