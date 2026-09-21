// node --test "tests/**/*.test.ts"  -- three directories deep, no framework, no DOM stack.
//
// E owns `apps/app/tests/e2e/`. The browser end-to-end suite itself is NOT here: F2 added no
// DOM test stack (08 §6) and there is no running console or Supabase project to drive, so
// every CONSOLE-* case E3/E4 will run is declared **pending** below with the reason. A
// pending case is never a pass (04: "unavailable external tests are pending, not skipped
// passes").
//
// What this file does prove, today:
//   1. a test three directories deep is discovered by the declared patterns (E2 acceptance:
//      "prove nested console tests ... are discovered");
//   2. an INTENTIONAL failure here is detected rather than skipped (INFRX_E2_CANARY=fail);
//   3. the pending inventory cannot rot: a case whose file has appeared must stop claiming
//      to be pending;
//   4. R48 hygiene in this directory - relative `.ts` imports only, never `@/`, never `.tsx`.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import test from "node:test";

const appRoot = new URL("../../", import.meta.url);
const here = new URL("./", import.meta.url);

/** The same glob subset `node --test` uses: `**` spans directories, `*` stays inside one. */
function globToRegExp(pattern: string): RegExp {
  const source = pattern
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*\//g, "\u0000")
    .replace(/\*/g, "[^/]*")
    .replace(/\u0000/g, "(?:[^/]+/)*");
  return new RegExp(`^${source}$`);
}

function declaredPatterns(): string[] {
  const script = (
    JSON.parse(readFileSync(new URL("package.json", appRoot), "utf8")) as {
      scripts: Record<string, string>;
    }
  ).scripts.test;
  return [...script.matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

const OWN_PATH = "tests/e2e/discovery.test.ts";

test("this file is three directories deep and is still discovered", () => {
  // The pre-F2 glob was `lib/*.test.ts`: it missed `tests/contracts/` at depth two and
  // would miss this at depth three. Asserting the match here means the E-owned directory is
  // covered by the patterns the console actually runs, not by a reviewer's assumption.
  const patterns = declaredPatterns();
  const matched = patterns.filter((pattern) => globToRegExp(pattern).test(OWN_PATH));
  assert.deepEqual(matched, ["tests/**/*.test.ts"], `no declared pattern runs ${OWN_PATH}`);
  assert.equal(OWN_PATH.split("/").length, 3, "depth three: tests / e2e / file");
  assert.ok(existsSync(new URL("discovery.test.ts", here)), "and the file is really here");
});

test("every test file E owns in this directory is matched by a declared pattern", () => {
  const expressions = declaredPatterns().map(globToRegExp);
  const missed = readdirSync(here, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(test|spec)\.(ts|tsx|mts|cts)$/.test(entry.name))
    .map((entry) => `tests/e2e/${entry.name}`)
    .filter((path) => !expressions.some((expression) => expression.test(path)));
  assert.deepEqual(missed, [], `these would never run: ${missed.join(", ")}`);
});

/**
 * The console cases E3/E4 must drive, from `research/traces/07-console-spec.md` §9 and
 * `research/traces/01-requirements.md` §2, under the namespacing of
 * `research/plan/04-verification.md` §1 (E2 publishes the full legacy map in its evidence).
 *
 * `file` is where the case will live. While that file does not exist the case is pending;
 * once it does, `pending` must be false or this test fails - which is what stops the
 * inventory from quietly outliving the work.
 */
const PENDING_CONSOLE_CASES: { id: string; legacy: string; file: string; why: string }[] = [
  {
    id: "CONSOLE-U1",
    legacy: "U1 (07 §9)",
    file: "lib/clickhouse.test.ts",
    why: "chQuery org binding needs C1's real ClickHouse client; E2 provides the service, C owns the module",
  },
  {
    id: "CONSOLE-U2",
    legacy: "U2 (07 §9)",
    file: "lib/s3.test.ts",
    why: "the SigV4 known vector needs the console's presigner, which does not exist yet",
  },
  {
    id: "CONSOLE-U3",
    legacy: "U3 (07 §9)",
    file: "lib/s3.test.ts",
    why: "gzip round-trip and the trace-content guard need the same module",
  },
  {
    id: "CONSOLE-U4",
    legacy: "U4 (07 §9)",
    file: "lib/types.test.ts",
    why: "parseFilters is V's pure view-model function; not written yet",
  },
  {
    id: "CONSOLE-E1",
    legacy: "E1 (07 §9, NOT plan task E1)",
    file: "tests/e2e/manual-checklist.test.ts",
    why: "a nine-step manual end-to-end against a deployed console; needs I2/I3, so E4 not E2",
  },
  {
    id: "CONSOLE-C7",
    legacy: "C7 (01 §2)",
    file: "tests/e2e/cross-tenant.test.ts",
    why: "the crafted-id / crafted-org attack needs C's server actions; the SQL-role half of the same rule is proved today by the DUR-RLS matrix in tests/integration/",
  },
];

test("the pending console inventory is honest and cannot rot", () => {
  assert.ok(PENDING_CONSOLE_CASES.length >= 6, "the console spec names more cases than this");
  const stale = PENDING_CONSOLE_CASES.filter((entry) =>
    existsSync(new URL(entry.file, appRoot)),
  ).map((entry) => `${entry.id}: ${entry.file} now exists but the case is still pending`);
  assert.deepEqual(stale, [], stale.join("; "));
  for (const entry of PENDING_CONSOLE_CASES) {
    assert.match(entry.id, /^CONSOLE-[A-Z]+\d+$/, `${entry.id} is not a namespaced id`);
    assert.ok(entry.why.length > 20, `${entry.id} must say why it cannot run yet`);
  }
  const ids = PENDING_CONSOLE_CASES.map((entry) => entry.id);
  assert.equal(new Set(ids).size, ids.length, "a case id is listed twice");
});

test("R48 import hygiene holds in the directory E owns", () => {
  for (const entry of readdirSync(here, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".ts")) continue;
    const source = readFileSync(new URL(entry.name, here), "utf8");
    const imports = [...source.matchAll(/from\s+"([^"]+)"/g)].map((match) => match[1]);
    for (const specifier of imports) {
      assert.ok(!specifier.startsWith("@/"), `${entry.name} imports ${specifier} via the alias`);
      assert.ok(!specifier.endsWith(".tsx"), `${entry.name} imports a .tsx module`);
    }
  }
});

test("canary: an intentional failure in the console runner is detected", () => {
  // run.py runs `pnpm test` with INFRX_E2_CANARY=fail and treats exit 0 as the failure: a
  // green run would mean node --test cannot see a broken console test.
  if (process.env.INFRX_E2_CANARY === "fail") {
    assert.fail("E2 canary: this failure is intentional (INFRX_E2_CANARY=fail)");
  }
  assert.ok(["", "off", undefined].includes(process.env.INFRX_E2_CANARY));
});
