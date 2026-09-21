#!/usr/bin/env node
// R32 for U1: every invariant the U1 cases name must be killable.
//
// Each mutant is one edit to a U1 view model, applied to a throwaway copy of the console. It is
// killed only when a case the mutant *declares* fails on an assertion. A mutant that breaks
// everything, or whose `find` text no longer matches, or that fails a case it did not name, tells us
// nothing about the invariant and fails the run instead.
//
// This is U1's own runner because the coordinator's `pnpm test:mutants`
// (tests/contracts/run-mutants.mjs) judges mutants by the exported ConsoleServices conformance
// suite, which is track C's contract and knows nothing about a page's view model.
//
// Usage: node tests/u/run-mutants.mjs [--only ID,ID] [--timeout MS] [--keep]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const only = flag("--only", null);
const timeoutMs = Math.max(1000, Number(flag("--timeout", "120000")) || 120000);
const keep = args.includes("--keep");

const USAGE = "app/(console)/usage/view-model.ts";
const BILLING = "app/(console)/billing/view-model.ts";

const SUITE = ["tests/u/usage-view-model.test.ts", "tests/u/billing-view-model.test.ts"];

const T = {
  url: "U1-T01 the URL is untrusted: an unrecognised range falls back, `all` means no filter",
  roundTrip: "U1-T02 serializing and parsing a filter state round-trips, and defaults stay out of the URL",
  query: "U1-T03 the query carries only contract fields, and the service accepts it",
  walk: "U1-T04 a keyset walk forward and back visits exactly the same pages, in order",
  cursorScope: "U1-T05 changing a filter discards the cursor, because a cursor is bound to its filters",
  states: "U1-T06 the state machine distinguishes loading, empty, ready and each recovery",
  hold: "U1-T07 an unsettled amount is never shown as a charge, and a hold has its own column",
  tokens: "U1-T08 unreported usage shows no token count and says so instead of estimating one",
  settlement: "U1-T09 each settlement state gets its own explanation",
  summary: "U1-T10 the summary reports charges and holds as different figures, formatted as money",
  exact: "U1-T11 money keeps all eight digits and timestamps are UTC whatever the locale",
  figures: "U1-T20 the three figures are the three the wallet reports, and available leads",
  identity: "U1-T21 available = promotional credit - reserved, and a wallet that disagrees is flagged",
  balanceState: "U1-T22 a new organization, an exhausted one and a funded one get different guidance",
  signed: "U1-T24 a credit reads as a credit and a debit keeps its sign",
  ledger: "U1-T25 every ledger kind renders, and an operator's entry names the platform, not a person",
  ledgerWalk: "U1-T26 the ledger walks forward and back on its own cursors",
};

/** One single edit each, and one named invariant each. */
const MUTANTS = [
  // --- money formatting -----------------------------------------------------
  {
    id: "U1-M01",
    what: "the summary shows the held total where the charged total belongs",
    file: USAGE,
    find: "      value: displayMoney(summary.cost),",
    replace: "      value: displayMoney(summary.pending_reconciliation),",
    cases: [T.summary],
  },
  {
    id: "U1-M02",
    what: "money is formatted through Number instead of the Money helpers",
    file: USAGE,
    find: "    charged: displayMoney(settled ? row.cost : ZERO_MONEY),",
    replace: "    charged: `$${Number(settled ? row.cost : ZERO_MONEY).toFixed(2)}`,",
    cases: [T.hold],
  },
  {
    id: "U1-M03",
    what: "a day's cost loses its precision to a float",
    file: USAGE,
    find: "    cost: displayMoney(day.cost),",
    replace: "    cost: `$${Number(day.cost).toFixed(2)}`,",
    cases: [T.exact],
  },
  {
    id: "U1-M04",
    what: "the balance card shows the granted total as the available figure",
    file: BILLING,
    find: "      value: displayMoney(balance.available),",
    replace: "      value: displayMoney(balance.ledger_total),",
    cases: [T.figures],
  },
  {
    id: "U1-M05",
    what: "a credit loses its plus sign and reads like a debit",
    file: BILLING,
    find: "  return isNegativeMoney(amount) || isZeroMoney(amount) ? shown : `+${shown}`;",
    replace: "  return shown;",
    cases: [T.signed],
  },

  // --- available computation display ---------------------------------------
  {
    id: "U1-M06",
    what: "the wallet identity is checked as a sum instead of a difference",
    file: BILLING,
    find:
      "    moneyUnits(balance.available) === moneyUnits(balance.ledger_total) - moneyUnits(balance.reserved_total)",
    replace:
      "    moneyUnits(balance.available) === moneyUnits(balance.ledger_total) + moneyUnits(balance.reserved_total)",
    cases: [T.identity],
  },
  {
    id: "U1-M07",
    what: "a spent-out organization is greeted as a brand-new one",
    file: BILLING,
    find: "  if (!hasHistory && isZeroMoney(balance.ledger_total)) {",
    replace: "  if (isZeroMoney(balance.ledger_total)) {",
    cases: [T.balanceState],
  },
  {
    id: "U1-M08",
    what: "an operator's ledger entry names the principal instead of the platform (R41)",
    file: BILLING,
    find: '  return actor === PLATFORM_ACTOR ? "infrx platform" : actor;',
    replace: "  return actor;",
    cases: [T.ledger],
  },

  // --- pagination state -----------------------------------------------------
  {
    id: "U1-M09",
    what: "stepping forward forgets the page it came from",
    file: USAGE,
    find: "    trail: state.cursor === null ? [] : [...state.trail, state.cursor],",
    replace: "    trail: [],",
    cases: [T.walk, T.ledgerWalk],
  },
  {
    id: "U1-M10",
    what: "stepping back reads the trail without consuming it",
    file: USAGE,
    find: "  const cursor = trail.pop() ?? null;",
    replace: "  const cursor = trail[trail.length - 1] ?? null;",
    cases: [T.walk, T.ledgerWalk],
  },
  {
    id: "U1-M11",
    what: "the page number stops counting the page being shown",
    file: USAGE,
    find: "  return state.trail.length + (state.cursor === null ? 1 : 2);",
    replace: "  return state.trail.length + 1;",
    cases: [T.walk, T.ledgerWalk],
  },
  {
    id: "U1-M12",
    what: "changing a filter keeps a cursor minted for the old filters",
    file: USAGE,
    find: "  return { ...filters, ...patch, cursor: null, trail: [] };",
    replace: "  return { ...filters, ...patch };",
    cases: [T.cursorScope],
  },
  {
    id: "U1-M13",
    what: "the cursor never reaches the query, so every page is page one",
    file: USAGE,
    find: '  if (filters.cursor !== null) query.cursor = filters.cursor;',
    replace: "  if (false) query.cursor = filters.cursor;",
    cases: [T.query, T.walk],
  },

  // --- filters and honesty about what was charged ---------------------------
  {
    id: "U1-M14",
    what: "a hold is rendered in the charged column",
    file: USAGE,
    find: "    charged: displayMoney(settled ? row.cost : ZERO_MONEY),",
    replace: "    charged: displayMoney(row.max_hold ?? (settled ? row.cost : ZERO_MONEY)),",
    cases: [T.hold],
  },
  {
    id: "U1-M15",
    what: "usage the engine did not report is shown as a token count anyway",
    file: USAGE,
    find: "    row.usage_certainty === \"unknown\" ||\n",
    replace: "",
    cases: [T.tokens],
  },
  {
    id: "U1-M16",
    what: "a platform-absorbed failure is presented as a settled charge",
    file: USAGE,
    find: '        label: "Platform absorbed",',
    replace: '        label: "Settled",',
    cases: [T.settlement],
  },
  {
    id: "U1-M17",
    what: "the model filter is dropped from the query, so a filtered view shows everything",
    file: USAGE,
    find: "  if (filters.model !== null) query.model = filters.model;",
    replace: "  if (false) query.model = filters.model;",
    cases: [T.query, T.states],
  },
  {
    id: "U1-M18",
    what: "the range is ignored and `from` becomes `to`",
    file: USAGE,
    find: "    from: new Date(now.getTime() - RANGES[filters.range]).toISOString(),",
    replace: "    from: new Date(now.getTime()).toISOString(),",
    cases: [T.query],
  },
  {
    id: "U1-M19",
    what: "an unrecognised range in the URL is passed to the service instead of falling back",
    file: USAGE,
    find: "    range: range !== null && range in RANGES ? (range as RangeKey) : DEFAULT_RANGE,",
    replace: "    range: (range ?? DEFAULT_RANGE) as RangeKey,",
    cases: [T.url],
  },
  {
    id: "U1-M20",
    what: "a stale page link offers a retry that cannot work instead of a restart",
    file: USAGE,
    find: '  if (code === "invalid_cursor") return "restart";',
    replace: '  if (code === "invalid_cursor") return "retry";',
    cases: [T.states, T.cursorScope],
  },
  {
    id: "U1-M21",
    what: "a refusal nobody can retry out of is offered as retryable",
    file: USAGE,
    find: "const RETRYABLE_STATUSES = [429, 500, 503, 504];",
    replace: "const RETRYABLE_STATUSES = [403, 429, 500, 503, 504];",
    cases: [T.states],
  },
  {
    id: "U1-M22",
    what: "an empty result is reported as ready, so a filter miss looks like a zero row",
    file: USAGE,
    find: "  return isEmpty(result.value) ? { kind: \"empty\" } : { kind: \"ready\", value: result.value };",
    replace: "  return { kind: \"ready\", value: result.value };",
    cases: [T.states],
  },
  {
    id: "U1-M23",
    what: "timestamps are rendered in the server's locale instead of UTC",
    file: USAGE,
    find: "  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;",
    replace: '  return new Date(iso).toLocaleString("en-US");',
    cases: [T.exact],
  },
  {
    id: "U1-M24",
    what: "the trail is dropped from the URL, so Previous cannot survive a reload",
    file: USAGE,
    find: '  for (const cursor of state.trail) search.append("trail", cursor);',
    replace: "  for (const cursor of []) search.append(\"trail\", cursor);",
    cases: [T.roundTrip],
  },
];

/**
 * Two checks on the runner itself, so a "killed" line means something: an edit that changes nothing
 * must survive, and an edit that breaks the file must be a runner error rather than a kill.
 */
const SELF = [
  {
    id: "SELF-NOOP",
    what: "an edit that changes nothing survives",
    file: USAGE,
    find: "export const DEFAULT_RANGE: RangeKey = \"24h\";",
    replace: "export const DEFAULT_RANGE: RangeKey = \"24h\"; // self-check no-op",
    cases: [T.url],
    expect: "survived",
  },
  {
    id: "SELF-BROKEN",
    what: "a file that does not parse is a runner error, not a kill",
    file: USAGE,
    find: "export const DEFAULT_RANGE: RangeKey = \"24h\";",
    replace: "export const DEFAULT_RANGE: RangeKey = ;",
    cases: [T.url],
    expect: "runner-error",
  },
];

function prepareCopy() {
  const root = mkdtempSync(join(tmpdir(), "u1-mutants-"));
  const app = join(root, "app");
  cpSync(appRoot, app, {
    recursive: true,
    dereference: false,
    filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
  });
  return { root, app };
}

/** The failing subtests and how each failed: a kill has to be an assertion, not a stray exception. */
function failingCases(out) {
  const cases = [];
  // Indent-agnostic: a top-level `test()` reports at column 0, a nested one indented.
  const pattern = /^ *not ok \d+ - (.*)$/gm;
  for (let match = pattern.exec(out); match !== null; match = pattern.exec(out)) {
    const rest = out.slice(match.index + match[0].length);
    const end = rest.search(/^ *\.\.\.$/m);
    const diagnostic = end === -1 ? rest : rest.slice(0, end);
    cases.push({
      name: match[1].trim(),
      how: /code: 'ERR_ASSERTION'/.test(diagnostic) ? "assertion" : "error",
    });
  }
  return cases;
}

function passingCases(out) {
  return [...out.matchAll(/^ *ok \d+ - (.*)$/gm)].map((match) => match[1].trim());
}

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
    const collect = (chunk) => {
      out += chunk;
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
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
  if (occurrences > 1) {
    return { ok: false, why: `its \`find\` text appears ${occurrences} times, so the edit is ambiguous` };
  }
  writeFileSync(join(app, mutant.file), pristine.replace(mutant.find, mutant.replace));
  return { ok: true };
}

function classify(mutant, run) {
  if (run.timedOut) return { outcome: "runner-error", why: "the suite did not finish in time" };
  if (!/^# tests \d+/m.test(run.out)) {
    return { outcome: "runner-error", why: "the suite produced no TAP summary" };
  }
  if (run.code !== 0 && run.failed.length === 0) {
    return { outcome: "runner-error", why: "the suite failed without naming a case" };
  }
  if (run.code === 0) return { outcome: "survived", why: "the suite passed" };
  if (run.passed.length === 0) {
    return { outcome: "runner-error", why: "every case failed, so the copy is broken" };
  }
  const matched = run.failed.filter((entry) => mutant.cases.includes(entry.name));
  if (matched.length === 0) {
    return {
      outcome: "survived",
      why: `the suite failed, but not in a declared case (failed: ${run.failed.map((c) => c.name).join("; ")})`,
    };
  }
  const byAssertion = matched.filter((entry) => entry.how === "assertion");
  if (byAssertion.length === 0) {
    return { outcome: "runner-error", why: "the declared case failed by exception, not by assertion" };
  }
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
let selfFailures = 0;
let survivors = 0;

for (const mutant of SELF) {
  const result = await judge(mutant);
  const ok = result.outcome === mutant.expect;
  if (!ok) selfFailures += 1;
  console.log(
    `${ok ? "ok  " : "FAIL"} ${mutant.id} self-check expected ${mutant.expect}, got ${result.outcome}` +
      `${result.why === undefined ? "" : ` (${result.why})`}`,
  );
}

for (const mutant of selected) {
  const result = await judge(mutant);
  if (result.outcome !== "killed") survivors += 1;
  console.log(
    `${result.outcome === "killed" ? "killed " : result.outcome.toUpperCase()} ${mutant.id} ${mutant.what}` +
      `${result.outcome === "killed" ? ` — by "${result.by}"` : ` — ${result.why}`}`,
  );
}

console.log(
  `\n${SELF.length} self-checks, ${SELF.length - selfFailures} as expected; ` +
    `${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`,
);
process.exit(selfFailures === 0 && survivors === 0 ? 0 : 1);
