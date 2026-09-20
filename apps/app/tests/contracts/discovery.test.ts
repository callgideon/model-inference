// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// F-BASE guard: an undiscovered test is a failed test setup, not a pass (04 §Test
// environments). This walks the console tree and fails if any *.test.ts file sits outside
// the glob patterns in the `test` script it reads from package.json.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

const appRoot = new URL("../../", import.meta.url);
/**
 * Build output and dependencies, skipped only where they actually live — at the console root. The
 * same names nested deeper are ordinary source directories (`app/(dashboard)/build/`,
 * `scripts/build/`), and skipping them at every depth hid a test file from this guard entirely.
 */
const SKIP_AT_ROOT = new Set(["node_modules", ".next", ".git", "out", "build", "coverage", "dist"]);

/** Every spelling someone might reach for; the guard's job is to catch the ones no glob runs. */
const TEST_SUFFIXES = [".test.ts", ".test.tsx", ".test.mts", ".test.cts", ".spec.ts", ".spec.tsx"];

function testFiles(directory = "", depth = 0): string[] {
  assert.ok(depth < 12, `refusing to walk deeper than 12 directories at ${directory}`);
  const found: string[] = [];
  for (const entry of readdirSync(new URL(directory, appRoot), { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (depth === 0 && SKIP_AT_ROOT.has(entry.name)) continue;
      if (entry.isSymbolicLink()) continue;
      found.push(...testFiles(`${directory}${entry.name}/`, depth + 1));
    } else if (TEST_SUFFIXES.some((suffix) => entry.name.endsWith(suffix))) {
      found.push(`${directory}${entry.name}`);
    }
  }
  return found.sort();
}

/** The subset of glob syntax `node --test` uses here: `**` for directories, `*` within one. */
function globToRegExp(pattern: string): RegExp {
  const source = pattern
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*\//g, "\u0000")
    .replace(/\*/g, "[^/]*")
    .replace(/\u0000/g, "(?:[^/]+/)*");
  return new RegExp(`^${source}$`);
}

const script = (
  JSON.parse(readFileSync(new URL("package.json", appRoot), "utf8")) as {
    scripts: Record<string, string>;
    engines?: Record<string, string>;
  }
).scripts.test;
const patterns = [...script.matchAll(/"([^"]+)"/g)].map((match) => match[1]);

test("the test script declares the four directory patterns of 08 §7", () => {
  assert.ok(script.startsWith("node --test"), `unexpected test runner: ${script}`);
  assert.deepEqual(patterns, [
    "lib/**/*.test.ts",
    "tests/**/*.test.ts",
    "app/**/*.test.ts",
    "components/**/*.test.ts",
  ]);
});

test("every test file in the tree is matched by a declared pattern", () => {
  const files = testFiles();
  const expressions = patterns.map(globToRegExp);
  const missed = files.filter((file) => !expressions.some((expression) => expression.test(file)));
  assert.deepEqual(
    missed,
    [],
    `these test files would never run — rename them to *.test.ts under a declared directory, or ask the coordinator to widen the patterns: ${missed.join(", ")}`,
  );
  assert.ok(files.length >= 6, `expected the console suites to be present, found ${files.length}`);
});

test("a test hidden in a directory named like build output is still flagged", () => {
  // The guard used to skip `build`, `out` and `coverage` at every depth, so `scripts/build/x.test.ts`
  // was neither run nor reported. Only the root-level directories are skipped now.
  assert.equal(SKIP_AT_ROOT.has("build"), true, "build output is skipped at the root");
  const files = testFiles();
  assert.ok(
    !files.some((file) => file.startsWith("node_modules/") || file.startsWith(".next/")),
    "dependencies and build output must not be walked",
  );
  for (const suffix of [".test.mts", ".spec.ts"]) {
    assert.ok(
      TEST_SUFFIXES.includes(suffix),
      `${suffix} must be recognised, so a file using it is reported rather than silently skipped`,
    );
  }
});

test("nested suites and the pre-existing library tests are both discovered", () => {
  const files = testFiles();
  // The four pre-F2 library tests live in these two files and must keep running (F-BASE).
  assert.ok(files.includes("lib/utils.test.ts"), "lib/utils.test.ts must stay discoverable");
  assert.ok(files.includes("lib/keys.test.ts"), "lib/keys.test.ts must stay discoverable");
  // This file is itself two directories deep: the old `lib/*.test.ts` glob missed it.
  assert.ok(files.includes("tests/contracts/discovery.test.ts"), "this nested file must be discovered");
  assert.ok(
    files.filter((file) => file.startsWith("tests/")).length >= 4,
    "the nested contract suite must be discovered",
  );
  assert.equal(globToRegExp("lib/**/*.test.ts").test("lib/utils.test.ts"), true, "** matches zero directories");
  assert.equal(
    globToRegExp("tests/**/*.test.ts").test("tests/contracts/discovery.test.ts"),
    true,
    "** matches one directory",
  );
  assert.equal(globToRegExp("lib/**/*.test.ts").test("tests/contracts/money.test.ts"), false);
});

test("the console pins a Node version that strips types", () => {
  const engines = (
    JSON.parse(readFileSync(new URL("package.json", appRoot), "utf8")) as {
      engines?: Record<string, string>;
    }
  ).engines;
  assert.equal(engines?.node, ">=22.18", "type stripping is unflagged from Node 22.18");
  const [major, minor] = process.versions.node.split(".").map(Number);
  assert.ok(major > 22 || (major === 22 && minor >= 18), `running on Node ${process.versions.node}`);
});
