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
const BOUNDARY = "app/(console)/usage/boundary.ts";
// The two error boundaries are `.tsx`, which `node --test` cannot load — the cases read them as
// source, so a mutant in them is still killable (R48 keeps the logic out of them either way).
const USAGE_ERROR = "app/(console)/usage/error.tsx";
const BILLING_ERROR = "app/(console)/billing/error.tsx";

// U1R: the CREDIT display helpers, read adapter and view models (credit-pg.test.ts needs a database
// and skips here; its world script is the real-PostgreSQL oracle).
const FORMAT = "lib/format.ts";
const READS = "app/(console)/billing/credit-reads.ts";
const CREDITS = "app/(console)/billing/credit-view-model.ts";
const JOBS = "app/(console)/usage/credit-view-model.ts";
const GATE = "app/(console)/usage/fake-console-context.ts";
const SOURCE = "app/(console)/billing/credit-fixture.ts";

// U4: the owned request detail — reads, view model, and the three route files the cases read as
// source (request-pg.test.ts needs a database and skips here; request_world.py is its oracle).
const RR = "app/(console)/usage/[requestId]/request-reads.ts";
const RV = "app/(console)/usage/[requestId]/request-view-model.ts";
const PANEL = "app/(console)/usage/[requestId]/result-panel.tsx";
const ROUTE = "app/(console)/usage/[requestId]/result/route.ts";
const PAGE = "app/(console)/usage/[requestId]/page.tsx";
const POLLER = "app/(console)/usage/[requestId]/status-poller.tsx";

// U3: the operator console (operator-postgrest.test.ts needs the stack and skips here;
// operator_stack.py is its real-database oracle). The page is `.tsx`: its cases read it as source.
const O_PORT = "app/(console)/admin/operator-port.ts";
const O_READS = "app/(console)/admin/operator-reads.ts";
const O_FORM = "app/(console)/admin/operator-form.ts";
const O_PAGE = "app/(console)/admin/page.tsx";
const O_FORMS = "app/(console)/admin/operator-forms.tsx";

const SUITE = [
  "tests/u/usage-view-model.test.ts",
  "tests/u/billing-view-model.test.ts",
  "tests/u/credit-format.test.ts",
  "tests/u/credit-reads.test.ts",
  "tests/u/credits-view-model.test.ts",
  "tests/u/usage-credits-view-model.test.ts",
  "tests/u/credit-preview-gate.test.ts",
  "tests/u/request-detail.test.ts",
  // U2
  "tests/u/keys-view-model.test.ts",
  "tests/u/keys-source.test.ts",
  "tests/u/settings-view-model.test.ts",
  "tests/u/operator-console.test.ts",
];

// U2: the API Keys and Settings page models and the two key controls (read as source by keys-source).
const KEYS_VM = "app/(console)/api-keys/view-model.ts";
const DIALOG = "app/(console)/api-keys/create-key-dialog.tsx";
const KEYS_PAGE = "app/(console)/api-keys/page.tsx";
const REVOKE = "app/(console)/api-keys/revoke-button.tsx";
const SETTINGS_VM = "app/(console)/settings/view-model.ts";
const SETTINGS_PAGE = "app/(console)/settings/page.tsx";

const T = {
  url: "U1-T01 the URL is untrusted: an unrecognised range falls back, `all` means no filter",
  roundTrip: "U1-T02 serializing and parsing a filter state round-trips, and defaults stay out of the URL",
  query: "U1-T03 the query carries only contract fields, and the service accepts it",
  walk: "U1-T04 a keyset walk forward and back visits exactly the same pages, in order",
  cursorScope: "U1-T05 changing a filter discards the cursor, because a cursor is bound to its filters",
  states: "U1-T06 the state machine distinguishes loading, empty, ready and each recovery",
  hold: "U1-T07 an unsettled amount is never shown as a charge, and a hold has its own column",
  tokens: "U1-T08 unreported usage shows no token count and says so instead of estimating one",
  settlement: "U1-T09 each settlement state gets its own label and its own explanation",
  summary: "U1-T10 the summary reports charges and holds as different figures, formatted as money",
  exact: "U1-T11 money keeps all eight digits and timestamps are UTC whatever the locale",
  figures: "U1-T20 the three figures are the three the wallet reports, and available leads",
  identity: "U1-T21 available = promotional credit - reserved, and a wallet that disagrees is flagged",
  balanceState: "U1-T22 a new organization, an exhausted one and a funded one get different guidance",
  signed: "U1-T24 a credit reads as a credit and a debit keeps its sign",
  ledger: "U1-T25 every ledger kind renders, and an operator's entry names the platform, not a person",
  ledgerWalk: "U1-T26 the ledger walks forward and back on its own cursors",
  prototype: "U1-T13 a prototype key is not a range, and no URL value reaches a prototype lookup",
  bounds: "U1-T14 a hand-written URL cannot grow the trail without bound",
  failedReads: "U1-T15 every failed read on the usage page is an error state, never an empty one",
  pageHrefs: "U1-T16 the page model computes every href and page number the markup renders",
  keyNotice:
    "U1-T17 a key filter naming a key of another organization is explained, not shown as silence",
  usageWording: "U1-T18 no usage-page string offers payment or calls promotional credit revenue",
  history: "U1-T27 a failed ledger read never makes an established organization look new",
  billingModel: "U1-T28 the billing page model states every branch, and page sizes are named here",
  boundary: "U1-T19 the error boundaries wire up the recovery that can actually recover",
  absences: "U1-T29 a legacy row and a deleted key render as absences, never as invented values",
  // U1R
  fExact: "U1R-F01 credits and legacy USD keep every one of the eight digits, with no float round-trip",
  fLabel: "U1R-F02 a CREDIT amount never carries a dollar sign and a USD amount is always labelled USD",
  fSigned: "U1R-F04 a signed ledger amount reads as a credit or a debit in its own unit",
  rWallet: "U1R-R01 the wallet is the caller's own consumer wallet, read exactly, and none is an explicit state",
  rLedger: "U1R-R02 the ledger is filtered to the caller's wallet, keyset-ordered, and a forged cursor never reaches a filter",
  rJobs: "U1R-R03 jobs page through consumer_jobs on its own cursor, one extra row decides the next page",
  rUnavailable: "U1R-R04 an error, a transport failure or an inexact row is an explicit failure, never a zero",
  rUnits: "U1R-R05 a job's unit follows its regime, and a CREDIT row labelled USD is refused",
  rCreditsIn: "U1R-R06 credits-in is a bounded read of the non-debit entries, and past the bound it is unknown",
  bFigures: "U1R-B01 available, reserved and spent are exact credits from their own fields, never dollars",
  bIdentity: "U1R-B02 available = balance - reserved, and a wallet that disagrees is flagged",
  bStates: "U1R-B03 no wallet, zero/negative, low and funded funds are four different states",
  bFailed: "U1R-B04 a failed wallet read is an error, never a zero, and a failed spent read is 'unavailable'",
  bLedger: "U1R-B06 every ledger kind renders signed in credits, a debit links to its request, no principal is shown",
  bLegacy: "U1R-B07 legacy USD is a separate USD section when history exists, and an error is not an empty history",
  bPage: "U1R-B08 the page model walks the ledger on its own cursors and states every branch",
  uHold: "U1R-U01 a pending or unreconciled job shows its hold and no charge; only a settled job shows a charged amount",
  uUnits: "U1R-U02 each row is in its own unit: credits never carry '$', legacy USD always says USD",
  uStatus: "U1R-U04 state, cause and settlement each read as what they are, and an unknown settlement is not a charge",
  uTokens: "U1R-U03 unreported usage shows no token count and says nothing is estimated",
  uWindow: "U1R-U05 the date window is cut on the ordered stream at its inclusive start, and ends the walk",
  uHrefs: "U1R-U06 every href is computed here: pages, window changes reset the cursor, rows link to their detail",
  uWalk: "U1R-U08 walking every page visits each job once, and the totals equal the wallet's spent and reserved",
  bSidebar: "U1R-B09 the sidebar figure is the wallet's exact available credits, never dollars, and a failed read is null",
  gGate: "U1R-G01 the CREDIT fixture gate opens only on an explicit development opt-in",
  gSource: "U1R-G02 with the gate closed the pages get the real session reads, never the fixture",
  gBuild: "U1R-G03 a production build never serves the CREDIT fixture whatever environment it is handed",
  // U2
  kGate: "U2-K01 only a ready, unsuspended individual is offered key creation, and every other state says why",
  kFailed: "U2-K02 a failed or missing key read is an unavailable state with a retry, never 'No keys yet'",
  kRows: "U2-K03 a row shows the stored prefix only, UTC times and whether it is revoked; only an active key is revocable",
  kSuspended: "U2-K04 a suspended individual keeps the list and can still revoke (R33)",
  kOnce: "U2-K05 the plaintext is shown only for a first, non-replayed creation; a replay or a failure never shows one",
  kCopy: "U2-K06 revocation and lost-key copy is the decided public text (P-26), never 'within a minute'",
  kLost: "U2-K07 a create or revoke call that rejects settles to a fixed notice, never a stuck control or the raw error",
  sSecret: "U2-S01 the plaintext secret is never stored, logged, put in a URL or sent anywhere but the screen",
  sActions: "U2-S02 the keys controls call the shared C3A actions; the leaky page-local actions are gone",
  sRead: "U2-S03 the keys page reads through the consumer session and never turns a failed read into an empty list",
  sCopy: "U2-S06 the one-time and lost-key copy is on screen where it applies, and closing the dialog forgets the plaintext",
  sSettings: "U2-S04 settings has no fake controls: nothing on it saves, toggles or posts",
  pFacts: "U2-P01 every privacy row is a fixed fact with its availability, and none is a control",
  pTruthful: "U2-P02 privacy copy states real serving retention and makes no zero-retention, never-stored or 120-second claim",
  pConsent: "U2-P03 sharing, annotation, evaluation and training are not offered, and signup grants no such permission",
  pAccount: "U2-P04 the account block shows the session's own e-mail and state; a failed load says so",
};

const O = {
  noEdit: "U3-S01 no arbitrary balance edit: admin/ writes no ledger row and holds no service key",
  gate: "U3-S02 the page refuses a non-operator (404) before any read, and reads with the session's own client",
  rpc: "U3-P01 each command is exactly one audited RPC; the actor is never sent (the DB derives it)",
  replay: "U3-P02 a replay is reported as a replay; an answer without a boolean is 'not confirmed', never success",
  grant: "U3-P03 the one-time signup grant is not an operator console operation: nothing is sent",
  refusals: "U3-P04 refusals map to typed codes with fixed text; DB detail never reaches the page",
  transport: "U3-P05 a transport failure or a client that cannot be built is 'not confirmed, retry with the same key'",
  accounts: "U3-R01 accounts are exact CREDIT strings joined to their organization's suspension state",
  malformed: "U3-R02 a figure that is not an exact decimal string, or does not reconcile, makes the section unavailable - never a zero",
  surface: "U3-R04 the reads touch only the operator read surface, bounded, with the documented filters",
  units: "U3-R05 an unknown-usage hold keeps its own unit: CREDIT for a credit job, USD for a legacy one",
  audit: "U3-R06 the audit trail is the closed action vocabulary; drift rows are exact CREDIT",
  key: "U3-F01 a form keeps its idempotency key across every failure and rotates it only after a committed change",
  outcome: "U3-F02 outcomes say what committed: once, already applied, refused, or not confirmed",
  input: "U3-F03 a form submits exactly the allowlisted fields of its operation, plus its key",
  wiring: "U3-S04 the form submits the key it holds and keeps it through nextKey(); a fresh key is only the initial state or the rotation",
  sections: "U3-S05 every page section goes through Section, and a failed read renders 'Unavailable', never its table",
};

/** One single edit each, and one named invariant each. */
const U4 = {
  job: "U4-R01 the job read asks consumer_jobs for exactly this request, as the session's own user",
  foreign: "U4-R02 a foreign, unknown, mismatched or malformed id is 'not found', and a malformed one never reaches the database",
  gate: "U4-R04 the result is served only when the API would serve it: the job read gates the content read",
  result: "U4-R05 each content refusal keeps its meaning: not found, not ready, expired, signed out, unavailable",
  response: "U4-R06 every result response is private and no-store, and only a ready one carries content",
  access: "U4-V01 result access is the contract's read classification of the persisted fields",
  failure: "U4-V03 a failure is explained with sanitized, actionable copy; the phase is waiting, running or finished",
  expiry: "U4-V04 content expiry keeps the request's metadata and charge but removes content access",
  poll: "U4-V05 polling backs off, is bounded, and runs only while the request (or a retryable read) is unfinished",
  client: "U4-V06 the browser trusts only a same-origin JSON answer; a redirect or 401 is a signed-out state",
  timer: "U4-V07 the browser drops content at the persisted expiry and never before it is due",
  loop: "U4-V09 the poll loop refreshes on the backoff, stops after MAX_POLLS, and unmount cancels it",
  watch: "U4-V10 an open page drops content when the persisted expiry is due, never before, re-arming past the timer ceiling",
  reads: "U4-V11 the result is read on mount and again on a back/forward restore (content hidden first); navigation aborts it",
  fetch: "U4-V12 the browser's result fetch is same-origin and no-store, and an abandoned read shows nothing",
  source: "U4-G01 the request fixture is reachable only through the development preview gate",
  surface: "U4-S01 content stays out of the page payload, browser storage and logs; the route answers no-store",
  wiring: "U4-S02 the client components run the tested drivers, no fetch bypasses them, and the page mounts the poller only when it polls",
};

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
    find:
      "    trail: state.cursor === null ? [] : [...state.trail, state.cursor].slice(-MAX_TRAIL_PAGES),",
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
  // --- r2: an absent value is rendered as absent, never invented -------------
  {
    id: "U1-M62",
    what: "a legacy row with no execution mode is shown as `sync`, a mode it never had",
    file: USAGE,
    find: "    mode: row.execution_mode ?? NO_VALUE,",
    replace: '    mode: row.execution_mode ?? "sync",',
    cases: [T.absences],
  },
  {
    id: "U1-M63",
    what: "a row with no outcome falls through to a job state it never had",
    file: USAGE,
    find: "    outcome: row.terminal_cause ?? row.job_state ?? NO_VALUE,",
    replace: '    outcome: row.terminal_cause ?? row.job_state ?? "succeeded",',
    cases: [T.absences],
  },
  {
    id: "U1-M64",
    what: "a deleted key is rendered as the empty string, so the column reads as a blank cell",
    file: USAGE,
    find: "    keyName: row.key_name ?? DELETED_KEY_LABEL,",
    replace: '    keyName: row.key_name ?? "",',
    cases: [T.absences],
  },
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
    find: "    from: new Date(now.getTime() - rangeMs(filters.range)).toISOString(),",
    replace: "    from: new Date(now.getTime()).toISOString(),",
    cases: [T.query],
  },
  {
    id: "U1-M19",
    what: "an unrecognised range in the URL is passed to the service instead of falling back",
    file: USAGE,
    find: "    range: isRangeKey(range) ? range : DEFAULT_RANGE,",
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
  // --- trust boundary: a URL value must never reach a prototype -------------
  {
    id: "U1-M25",
    what: "`in` is used again, so every Object.prototype member passes as a range key",
    file: USAGE,
    find: "  return value !== null && Object.hasOwn(RANGES, value);",
    replace: "  return value !== null && value in RANGES;",
    cases: [T.prototype],
  },
  {
    id: "U1-M26",
    what: "the range table is read without an own-property check, so a forged key throws",
    file: USAGE,
    find: "  return Object.hasOwn(RANGES, range) ? RANGES[range] : RANGES[DEFAULT_RANGE];",
    replace: "  return RANGES[range];",
    cases: [T.prototype],
  },
  {
    id: "U1-M27",
    what: "an error hint is looked up straight off the prototype chain",
    file: USAGE,
    find: "  return (Object.hasOwn(HINTS, code) ? HINTS[code] : undefined) ?? fallback;",
    replace: "  return HINTS[code] ?? fallback;",
    cases: [T.prototype],
  },
  {
    id: "U1-M28",
    what: "an out-of-vocabulary settlement state falls out of the switch as undefined",
    file: USAGE,
    find: '        detail: "We cannot explain this request\'s settlement. Nothing is presented as charged.",',
    replace: "        detail: undefined as unknown as string,",
    cases: [T.prototype],
  },
  {
    id: "U1-M29",
    what: "an over-long cursor from the URL is accepted",
    file: USAGE,
    find: "  return value !== null && value.length <= MAX_CURSOR_CHARS ? value : null;",
    replace: "  return value;",
    cases: [T.bounds],
  },
  {
    id: "U1-M30",
    what: "the parsed trail is no longer capped",
    file: USAGE,
    find: "          .slice(-MAX_TRAIL_PAGES);",
    replace: "          .slice();",
    cases: [T.bounds],
  },
  {
    id: "U1-M31",
    what: "a trail is kept although there is no cursor, so page one carries a forged back-stack",
    file: USAGE,
    find: "      ? []\n      : many(params.trail)",
    replace: "      ? many(params.trail)\n      : many(params.trail)",
    cases: [T.bounds],
  },
  {
    id: "U1-M32",
    what: "walking forward grows the trail without a cap",
    file: USAGE,
    find:
      "    trail: state.cursor === null ? [] : [...state.trail, state.cursor].slice(-MAX_TRAIL_PAGES),",
    replace: "    trail: state.cursor === null ? [] : [...state.trail, state.cursor],",
    cases: [T.bounds],
  },

  // --- claimed invariants the first review found unkillable ------------------
  {
    id: "U1-M33",
    what: "the cost is read whatever the settlement state says (survived review round 1)",
    file: USAGE,
    find: "    charged: displayMoney(settled ? row.cost : ZERO_MONEY),",
    replace: "    charged: displayMoney(row.cost),",
    cases: [T.hold],
  },
  {
    id: "U1-M34",
    what: "a free failure is given the platform-absorbed explanation",
    file: USAGE,
    find: '        detail: "The request ended before it produced billable work, so the hold was released.",',
    replace: '        detail: "The failure was ours. The hold was released and you were not charged.",',
    cases: [T.settlement],
  },
  {
    id: "U1-M35",
    what: "a request still running is told it has been charged",
    file: USAGE,
    find:
      "        detail: `This request is ${row.job_state}. Nothing has been charged, and the hold is a ceiling rather than a price.`,",
    replace: '        detail: "This has been charged.",',
    cases: [T.settlement],
  },
  {
    id: "U1-M36",
    what: "a missing token count on an authoritative row is rendered as zero",
    file: USAGE,
    find: "    row.prompt_tokens === null ||\n    row.completion_tokens === null\n",
    replace: "    false\n",
    cases: [T.tokens],
  },
  {
    id: "U1-M37",
    what: "the outcome column ignores the terminal cause",
    file: USAGE,
    // r2 re-expressed: the job state is nullable now, so the fallback chain ends in NO_VALUE.
    find: "    outcome: row.terminal_cause ?? row.job_state ?? NO_VALUE,",
    replace: "    outcome: row.job_state ?? NO_VALUE,",
    cases: [T.settlement],
  },
  {
    id: "U1-M38",
    what: "a funded organization with an unreadable ledger is greeted as brand new",
    file: BILLING,
    find: "  if (!hasHistory && isZeroMoney(balance.ledger_total)) {",
    replace: "  if (!hasHistory) {",
    cases: [T.balanceState],
  },
  {
    id: "U1-M39",
    what: "the exhausted state stops saying where credit comes from",
    file: BILLING,
    find:
      "      guidance: `Your grants are fully spent or reserved by requests in flight. ${ASK_OPERATOR}`,",
    replace: '      guidance: "Your grants are fully spent or reserved by requests in flight.",',
    cases: [T.balanceState],
  },

  // --- B3: a failed read must never render as an empty one -------------------
  {
    id: "U1-M40",
    what: "a failed daily read is rendered as an empty chart",
    file: USAGE,
    find: "    daily: mapState(viewStateOf(input.daily, (days) => days.length === 0), dayViews),",
    replace:
      "    daily: mapState(viewStateOf({ ok: true, value: input.daily.ok ? input.daily.value : [] }, (days) => days.length === 0), dayViews),",
    cases: [T.failedReads],
  },
  {
    id: "U1-M41",
    what: "a failed balances read makes the card vanish instead of reporting the failure",
    file: BILLING,
    find: "  return mapState(viewStateOf(balance, () => false), (wallet) =>",
    replace:
      "  if (!balance.ok) return { kind: \"empty\" };\n  return mapState(viewStateOf(balance, () => false), (wallet) =>",
    cases: [T.history],
  },
  {
    id: "U1-M42",
    what: "a failed ledger read is taken as proof that there is no history",
    file: BILLING,
    find: "  if (!ledger.ok) return true;",
    replace: "  if (!ledger.ok) return false;",
    cases: [T.history],
  },
  {
    id: "U1-M43",
    what: "an unknown key filter is passed over in silence",
    file: USAGE,
    find: "  if (filters.keyId === null || !keys.ok) return null;",
    replace: "  return null;\n  // eslint-disable-next-line",
    cases: [T.keyNotice],
  },
  {
    id: "U1-M44",
    what: "the ledger page size stops being the one the view model names",
    file: BILLING,
    find: "  return { limit: LEDGER_PAGE_SIZE, ...(state.cursor === null ? {} : { cursor: state.cursor }) };",
    replace: "  return { limit: 1, ...(state.cursor === null ? {} : { cursor: state.cursor }) };",
    cases: [T.billingModel],
  },
  {
    id: "U1-M45",
    what: "the pager's Previous link is offered on page one",
    file: USAGE,
    find: "        previousHref: hasPreviousPage(filters) ? usageHref(previousCursorState(filters)) : null,",
    replace: "        previousHref: usageHref(previousCursorState(filters)),",
    cases: [T.pageHrefs],
  },
  {
    id: "U1-M46",
    what: "the retry target is the first page rather than the page being shown",
    file: USAGE,
    find: "    here: usageHref(filters),",
    replace: "    here: firstHref,",
    cases: [T.pageHrefs],
  },
  // --- review round 2 --------------------------------------------------------
  {
    id: "U1-M47",
    what: "the ledger kind label is read off the prototype chain (survived review round 2)",
    file: BILLING,
    find: "  return Object.hasOwn(KIND_LABELS, kind) ? KIND_LABELS[kind] : UNKNOWN_KIND_LABEL;",
    replace: "  return KIND_LABELS[kind];",
    cases: [T.ledger],
  },
  {
    id: "U1-M48",
    what: "the usage boundary goes back to `reset`, which cannot re-fetch a server throw",
    file: USAGE_ERROR,
    find: "export default function UsageError({ retry }: { error: Error & { digest?: string }; retry: () => void }) {",
    replace:
      "export default function UsageError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {",
    cases: [T.boundary],
  },
  {
    id: "U1-M49",
    what: "the balance boundary's button calls `reset()` instead of `retry()`",
    file: BILLING_ERROR,
    find: "        <Button variant=\"outline\" size=\"sm\" onClick={() => retry()}>",
    replace: "        <Button variant=\"outline\" size=\"sm\" onClick={() => reset()}>",
    cases: [T.boundary],
  },
  {
    id: "U1-M50",
    what: "the boundary stops telling the reader that nothing was charged by the failure",
    file: BOUNDARY,
    find: '  usage: "Your requests and your balance are unaffected — nothing here changes accounting.",',
    replace: '  usage: "Your requests and your balance are fine.",',
    cases: [T.boundary],
  },
  {
    id: "U1-M51",
    what: "the cursor bound becomes exclusive, so a real maximum-length cursor is refused",
    file: USAGE,
    find: "  return value !== null && value.length <= MAX_CURSOR_CHARS ? value : null;",
    replace: "  return value !== null && value.length < MAX_CURSOR_CHARS ? value : null;",
    cases: [T.bounds],
  },
  {
    id: "U1-M52",
    what: "the usage pager offers Next on the last page",
    file: USAGE,
    find:
      "        nextHref:\n          page.next_cursor === null\n            ? null\n            : usageHref(nextCursorState(filters, page.next_cursor)),",
    replace: '        nextHref: usageHref(nextCursorState(filters, page.next_cursor ?? "")),',
    cases: [T.pageHrefs],
  },
  {
    id: "U1-M53",
    what: "the ledger pager offers Next on the last page",
    file: BILLING,
    find:
      "        nextHref:\n          value.next_cursor === null\n            ? null\n            : ledgerHref(nextCursorState(input.state, value.next_cursor)),",
    replace: '        nextHref: ledgerHref(nextCursorState(input.state, value.next_cursor ?? "")),',
    cases: [T.billingModel],
  },
  {
    id: "U1-M54",
    what: "being on a later page stops counting as ledger history",
    file: BILLING,
    find: "  return ledger.value.items.length > 0 || state.cursor !== null;",
    replace: "  return ledger.value.items.length > 0;",
    cases: [T.history, T.billingModel],
  },
  {
    id: "U1-M55",
    what: "clearing the key filter keeps the cursor minted for the filtered walk",
    file: USAGE,
    find:
      "      filters.keyId === null ? null : usageHref(withFilter(filters, { keyId: null })),",
    replace: "      filters.keyId === null ? null : usageHref({ ...filters, keyId: null }),",
    cases: [T.pageHrefs],
  },
  {
    id: "U1-M56",
    what: "a row reporting only one of the two token counts shows that one as a count",
    file: USAGE,
    find: "    row.completion_tokens === null\n",
    replace: "    false\n",
    cases: [T.tokens],
  },
  // --- review round 3: the edits that survived the suite as written ----------
  // The reviewer's ids are given so a kill can be cross-checked against their corpus.
  {
    id: "U1-M57",
    what: "the balance card ignores the page cursor when deciding history (reviewer N34)",
    file: BILLING,
    find: "    balanceCardModel(wallet, hasLedgerHistory(ledger, state)),",
    replace: "    balanceCardModel(wallet, hasLedgerHistory(ledger)),",
    cases: [T.billingModel],
  },
  {
    id: "U1-M58",
    what: "the unknown-kind label becomes the blank cell the guard exists to prevent (reviewer K02)",
    file: BILLING,
    find: 'export const UNKNOWN_KIND_LABEL = "Other";',
    replace: 'export const UNKNOWN_KIND_LABEL = "";',
    cases: [T.ledger],
  },
  {
    id: "U1-M59",
    what: "the boundary copy's scope guard is removed, so an unknown scope interpolates an object (reviewer E07)",
    file: BOUNDARY,
    find: '  const known: BoundaryScope = Object.hasOwn(UNAFFECTED, scope) ? scope : "usage";',
    replace: "  const known: BoundaryScope = scope;",
    cases: [T.boundary],
  },
  {
    id: "U1-M60",
    what: "the boundary puts the thrown error's own digest on the page (reviewer E04)",
    file: USAGE_ERROR,
    find: "        <p className=\"text-sm text-muted-foreground\">{copy.detail}</p>",
    replace:
      "        <p className=\"text-sm text-muted-foreground\">{copy.detail}</p>\n        <p>{String(error)}</p>",
    cases: [T.boundary],
  },
  {
    id: "U1-M61",
    what: "clearing the key filter clears the model filter with it (reviewer P04)",
    file: USAGE,
    find:
      "      filters.keyId === null ? null : usageHref(withFilter(filters, { keyId: null })),",
    replace:
      "      filters.keyId === null ? null : usageHref(withFilter(filters, { keyId: null, model: null })),",
    cases: [T.pageHrefs],
  },
  // --- U1R: CREDIT display, read adapter, Credits and Usage view models ------------------------
  { id: "U1R-M01", what: "credits are formatted through Number", file: FORMAT,
    find: "  return displayCredit(parseCredit(value));",
    replace: "  return `${Number(value).toLocaleString(\"en-US\", { minimumFractionDigits: 2 })} credits`;",
    cases: [T.fExact] },
  { id: "U1R-M02", what: "legacy USD loses its USD label", file: FORMAT,
    find: "  return `${displayMoney(parseMoney(value))} USD`;",
    replace: "  return displayMoney(parseMoney(value));",
    cases: [T.fExact, T.fLabel] },
  { id: "U1R-M03", what: "a ledger credit loses its plus sign", file: FORMAT,
    find: "? shown : `+${shown}`;", replace: "? shown : shown;", cases: [T.fSigned] },
  { id: "U1R-M04", what: "the wallet read is not scoped to the signed-in owner (an operator sees any wallet)", file: READS,
    find: "          .eq(\"owner_user_id\", userId)\n", replace: "", cases: [T.rWallet] },
  { id: "U1R-M05", what: "the ledger read is not scoped to the caller's wallet", file: READS,
    find: "        .eq(\"wallet_id\", walletId);", replace: ";", cases: [T.rLedger] },
  { id: "U1R-M06", what: "any cursor text is spliced into the PostgREST filter", file: READS,
    find: "Z)\\|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;",
    replace: "Z)\\|(.+)$/;", cases: [T.rLedger] },
  { id: "U1R-M07", what: "the ledger asks for no probe row, so it never has a next page", file: READS,
    find: "          .limit(page.limit + 1),", replace: "          .limit(page.limit),", cases: [T.rLedger] },
  { id: "U1R-M08", what: "the jobs read asks for no probe row", file: READS,
    find: "p_limit: page.limit + 1 }", replace: "p_limit: page.limit }", cases: [T.rJobs] },
  { id: "U1R-M09", what: "a JSON number is accepted as a wallet amount", file: READS,
    find: "  return parseCredit(text(row, name));",
    replace: "  const v = field(row, name);\n  return parseCredit(typeof v === \"number\" ? v.toFixed(8) : v);",
    cases: [T.rUnavailable] },
  { id: "U1R-M10", what: "a permission refusal reads as an outage", file: READS,
    find: "if (error.code === \"42501\")", replace: "if (error.code === \"x42501\")", cases: [T.rUnavailable] },
  { id: "U1R-M11", what: "a job's stated unit is not checked against its regime", file: READS,
    find: "  if (text(row, \"unit\") !== unit) throw", replace: "  if (false) throw", cases: [T.rUnits] },
  { id: "U1R-M12", what: "credits-in sums a partial read past its bound", file: READS,
    find: "found.length > CREDITS_IN_BOUND ? null :", replace: "false ? null :", cases: [T.rCreditsIn] },
  { id: "U1R-M13", what: "the Available figure shows the balance, ignoring holds", file: CREDITS,
    find: "      value: credits(wallet.available),", replace: "      value: credits(wallet.ledgerTotal),", cases: [T.bFigures] },
  { id: "U1R-M14", what: "a wallet that does not reconcile is never flagged", file: CREDITS,
    find: "  return subCredit(wallet.ledgerTotal, wallet.reservedTotal) === wallet.available;",
    replace: "  return true;", cases: [T.bIdentity] },
  { id: "U1R-M15", what: "exactly zero available is not exhausted", file: CREDITS,
    find: "compareCredit(wallet.available, ZERO_CREDIT) <= 0", replace: "compareCredit(wallet.available, ZERO_CREDIT) < 0",
    cases: [T.bStates] },
  { id: "U1R-M16", what: "an unreadable spent total is shown as zero", file: CREDITS,
    find: "      value: spent === null ? UNAVAILABLE : credits(spent),",
    replace: "      value: credits(spent ?? ZERO_CREDIT),", cases: [T.bFailed] },
  { id: "U1R-M17", what: "legacy USD history with a zero balance is hidden", file: CREDITS,
    find: "(value) => value.entryCount === 0)", replace: "(value) => value.balance === \"0.00000000\")",
    cases: [T.bLegacy] },
  { id: "U1R-M18", what: "a failed wallet read leaves the ledger looking empty", file: CREDITS,
    find: "          input.wallet.ok\n", replace: "          true\n", cases: [T.bPage] },
  { id: "U1R-M19", what: "the ledger page selects actor again (visible_principal() on every wallet row)", file: READS,
    find: '.select("entry_id, created_at, kind, amount, unit, request_id, reason")',
    replace: '.select("entry_id, created_at, kind, amount, unit, request_id, reason, actor")', cases: [T.rLedger] },
  { id: "U1R-M20", what: "the request link is not encoded", file: CREDITS,
    find: "  return `/usage/${encodeURIComponent(requestId)}`;", replace: "  return `/usage/${requestId}`;",
    cases: [T.bLedger] },
  { id: "U1R-M21", what: "a pending job shows a zero charge", file: JOBS,
    find: "        label: \"Pending\",\n        amount: null,", replace: "        label: \"Pending\",\n        amount: amount(\"0\", job.unit),",
    cases: [T.uHold] },
  { id: "U1R-M22", what: "a settled job whose charge is unreadable shows zero", file: JOBS,
    find: "        amount: job.charged === null ? \"Unavailable\" : amount(job.charged, job.unit),",
    replace: "        amount: amount(job.charged ?? \"0\", job.unit),", cases: [T.uHold] },
  { id: "U1R-M23", what: "a released hold is still shown as held", file: JOBS,
    find: "(job.holdState === \"held\" || job.holdState === \"unknown\")", replace: "job.holdState !== null",
    cases: [T.uStatus] },
  { id: "U1R-M24", what: "a legacy USD job is labelled credits", file: JOBS,
    find: "    unit: job.unit === \"CREDIT\" ? \"credits\" : \"legacy USD\",", replace: "    unit: \"credits\",",
    cases: [T.uUnits] },
  { id: "U1R-M25", what: "unauthoritative token counts are shown as usage", file: JOBS,
    find: "  if (job.usageCertainty !== \"authoritative\" || ", replace: "  if (", cases: [T.uTokens] },
  { id: "U1R-M26", what: "the window's start is exclusive", file: JOBS,
    find: "job.createdAt >= start", replace: "job.createdAt > start", cases: [T.uWindow] },
  { id: "U1R-M27", what: "a page that crossed the window's start keeps its next link", file: JOBS,
    find: "{ items: kept, next_cursor: null }", replace: "{ items: kept, next_cursor: page.next_cursor }",
    cases: [T.uWindow] },
  { id: "U1R-M28", what: "changing the window keeps the old cursor", file: JOBS,
    find: "  return { range, cursor: null, trail: [] };", replace: "  return { ...filters, range };", cases: [T.uHrefs] },
  { id: "U1R-M29", what: "the next page resumes from the first row instead of the last", file: READS,
    find: "cursors[shown.length - 1]", replace: "cursors[0]", cases: [T.rJobs] },
  // Fix round (0-U1R-V-01): the production/preview seam in front of the CREDIT fixture.
  { id: "U1R-M30", what: "the fixture gate is forced open (reviewer P1)", file: GATE,
    find: "  return consoleContext(env) !== null;", replace: "  return true;", cases: [T.gGate] },
  { id: "U1R-M31", what: "the fixture is chosen whatever the gate says (reviewer P2)", file: SOURCE,
    find: "  if (previewAllowed(env)) {", replace: "  if (true) {", cases: [T.gSource, T.gBuild] },
  // Fix round (1-U1R-V02 / WR-2): the sidebar figure the layout will render.
  { id: "U1R-M32", what: "a failed wallet read shows zero credits in the sidebar", file: CREDITS,
    find: "  if (!wallet.ok) return null;", replace: "  if (!wallet.ok) return credits(\"0\");", cases: [T.bSidebar] },
  { id: "U1R-M33", what: "the sidebar shows the balance, ignoring holds", file: CREDITS,
    find: "? \"No credits yet\" : credits(wallet.value.available);", replace: "? \"No credits yet\" : credits(wallet.value.ledgerTotal);",
    cases: [T.bSidebar] },
  // U4: owned request detail and result lifecycle.
  { id: "U4-M01", what: "the job read is not narrowed to the requested id", file: RR,
    find: "rpc: (fn, args) => client.rpc(fn, { ...args, p_request_id: id }),", replace: "rpc: (fn, args) => client.rpc(fn, args),",
    cases: [U4.job] },
  { id: "U4-M02", what: "a row for another request is shown as this one", file: RR,
    find: "found !== undefined && found.requestId === id ? found : null", replace: "found ?? null", cases: [U4.foreign] },
  { id: "U4-M03", what: "a malformed id reaches the database", file: RR,
    find: "  return REQUEST_ID.test(id) ? id : null;", replace: "  return id;", cases: [U4.foreign] },
  { id: "U4-M04", what: "content is read whatever the API would answer (held_unknown, expired, pending)", file: RR,
    find: "      if (access !== \"available\") return WITHHELD[access];", replace: "", cases: [U4.gate] },
  { id: "U4-M05", what: "an expired result reads as not ready", file: RR,
    find: "if (message.startsWith(\"result_expired:\")) return { state: \"expired\" };",
    replace: "if (message.startsWith(\"result_expired:\")) return { state: \"pending\" };", cases: [U4.result] },
  { id: "U4-M06", what: "a refused JWT is an outage, not a signed-out session", file: RR,
    find: "if (error.code === \"42501\" || /^PGRST30\\d$/.test(error.code ?? \"\")) return { state: \"signed_out\" };",
    replace: "if (false) return { state: \"signed_out\" };", cases: [U4.result] },
  { id: "U4-M07", what: "the result response may be cached", file: RR,
    find: "\"Cache-Control\": \"private, no-store, max-age=0\"", replace: "\"Cache-Control\": \"private, max-age=60\"",
    cases: [U4.response] },
  { id: "U4-M08", what: "a non-text body is served as content", file: RR,
    find: "typeof answer.data === \"string\" ? { state: \"ready\", text: answer.data }",
    replace: "answer.data !== null ? { state: \"ready\", text: answer.data as string }", cases: [U4.result] },
  { id: "U4-M09", what: "the request fixture ignores the preview gate", file: RR,
    find: "  if (previewAllowed(env)) return { reads: fixtureRequestReads(), preview: true };",
    replace: "  if (true) return { reads: fixtureRequestReads(), preview: true };", cases: [U4.source] },
  { id: "U4-M10", what: "a job read refused for a missing JWT subject is an outage", file: RR,
    find: "read.error.code === \"forbidden\" ? \"signed_out\" : \"unavailable\"", replace: "\"unavailable\"",
    cases: [U4.gate] },
  { id: "U4-M11", what: "available vs expired ignores the database's persisted-expiry verdict", file: RV,
    find: "  return job.resultAvailable ? \"available\" : \"expired\";", replace: "  return \"available\";",
    cases: [U4.access, U4.expiry] },
  { id: "U4-M12", what: "an unknown-usage request's result is served", file: RV,
    find: "  if (job.settlementState === \"held_unknown\") return \"held_unknown\";", replace: "", cases: [U4.access] },
  { id: "U4-M13", what: "a success without authoritative usage is served", file: RV,
    find: "if (job.state !== \"succeeded\" || job.usageCertainty !== \"authoritative\") return \"no_result\";",
    replace: "if (job.state !== \"succeeded\") return \"no_result\";", cases: [U4.access] },
  { id: "U4-M14", what: "a success with no persisted expiry is treated as expiring", file: RV,
    find: "  if (job.resultExpiresAt === null) return \"unavailable\";", replace: "", cases: [U4.access] },
  { id: "U4-M15", what: "polling never stops", file: RV,
    find: "attempt >= MAX_POLLS) return null;", replace: "attempt >= Infinity) return null;", cases: [U4.poll] },
  { id: "U4-M16", what: "polling does not back off", file: RV,
    find: "Math.min(FIRST_POLL_MS * 2 ** attempt, MAX_POLL_MS)", replace: "FIRST_POLL_MS", cases: [U4.poll] },
  { id: "U4-M17", what: "a finished request keeps polling", file: RV,
    find: "    poll: phase === \"waiting\" || phase === \"running\",", replace: "    poll: true,", cases: [U4.poll] },
  { id: "U4-M18", what: "a refusal is re-polled like an outage", file: RV,
    find: "poll: state.recovery === \"retry\"", replace: "poll: true", cases: [U4.poll] },
  { id: "U4-M19", what: "a redirected fetch (sent to /login) is trusted", file: RV,
    find: "if (response.redirected || response.status === 401)", replace: "if (response.status === 401)", cases: [U4.client] },
  { id: "U4-M20", what: "a body whose state disagrees with its status is trusted", file: RV,
    find: "  if (RESULT_STATUS[state as ResultRead[\"state\"]] !== response.status) return { state: \"unavailable\" };",
    replace: "", cases: [U4.client] },
  { id: "U4-M21", what: "content outlives the persisted expiry instant", file: RV,
    find: "return Math.min(Math.max(at - nowMs, 0), MAX_TIMER_MS);", replace: "return Math.min(Math.max(at - nowMs + 1, 0), MAX_TIMER_MS);",
    cases: [U4.timer] },
  { id: "U4-M22", what: "a platform failure shows its internal cause", file: RV,
    find: "  return { title: \"The request failed on our side.\", action: OURS };",
    replace: "  return { title: `The request failed: ${cause}.`, action: OURS };", cases: [U4.failure] },
  { id: "U4-M23", what: "the browser may cache the result fetch", file: RV,
    find: "      cache: \"no-store\",", replace: "      cache: \"force-cache\",", cases: [U4.fetch] },
  { id: "U4-M24", what: "the result panel persists content in browser storage", file: PANEL,
    find: "    const text = shown.text;", replace: "    const text = shown.text;\n    localStorage.setItem(\"result\", text);",
    cases: [U4.surface] },
  { id: "U4-M25", what: "the result route may be prerendered or cached", file: ROUTE,
    find: "export const dynamic = \"force-dynamic\";", replace: "export const dynamic = \"auto\";", cases: [U4.surface] },
  // U4 fix round (0-U4-V-01/02): the browser lifecycle drivers and the components' wiring of them.
  // A, B, F, K, D and E are the review's surviving mutants, each in its driver and its wiring form.
  { id: "U4-M26", what: "(A) an open page keeps content past its expiry", file: RV,
    find: "    if (delay === 0) return onExpire();", replace: "    if (false) return onExpire();", cases: [U4.watch] },
  { id: "U4-M27", what: "(A) the panel does not drop content when the expiry fires", file: PANEL,
    find: "() => setShown({ state: \"expired\" })", replace: "() => {}", cases: [U4.wiring] },
  { id: "U4-M28", what: "(B) a back/forward restore shows the old content without re-reading", file: RV,
    find: "    if ((event as Event & { persisted?: boolean }).persisted) {", replace: "    if (false) {", cases: [U4.reads] },
  { id: "U4-M29", what: "(B) the panel listens for restores on something other than the window", file: PANEL,
    find: "setShown, window)", replace: "setShown, new EventTarget())", cases: [U4.wiring] },
  { id: "U4-M30", what: "(F) navigation away does not abort the reads", file: RV,
    find: "    controller.abort();\n", replace: "", cases: [U4.reads] },
  { id: "U4-M31", what: "(F) the panel drops the driver's cleanup, so nothing is aborted", file: PANEL,
    find: "useEffect(() => watchResult(", replace: "useEffect(() => void watchResult(", cases: [U4.wiring] },
  { id: "U4-M32", what: "(K) the retry button fetches without no-store", file: PANEL,
    find: "            readResult(requestId).then((read) => {",
    replace: "            fetch(`/usage/${requestId}/result`).then((r) => r.json()).then((read) => {", cases: [U4.wiring] },
  { id: "U4-M33", what: "(E) the poll loop never stops", file: RV,
    find: "    const delay = pollDelayMs(attempt);", replace: "    const delay = pollDelayMs(attempt) ?? FIRST_POLL_MS;", cases: [U4.loop] },
  { id: "U4-M34", what: "(E) the poller never says it stopped", file: POLLER,
    find: "() => setStopped(true)", replace: "() => {}", cases: [U4.wiring] },
  { id: "U4-M35", what: "(D) the page polls whatever the model says", file: PAGE,
    find: "{pollsFor(model) ? <StatusPoller /> : null}", replace: "{true ? <StatusPoller /> : null}", cases: [U4.wiring] },
  { id: "U4-M36", what: "(D) a not-found request or a refused read is polled", file: RV,
    find: "model.kind === \"error\" && model.poll;", replace: "true;", cases: [U4.poll] },
  { id: "U4-M37", what: "unmount does not cancel the next poll", file: RV,
    find: "  arm(0);\n  return () => cancel();", replace: "  arm(0);\n  return () => {};", cases: [U4.loop] },
  { id: "U4-M38", what: "an early timer drops content before its expiry", file: RV,
    find: "    cancel = schedule(arm, delay);", replace: "    cancel = schedule(onExpire, delay);", cases: [U4.watch] },
  { id: "U4-M39", what: "unmount does not clear the expiry timer", file: RV,
    find: "  arm();\n  return () => cancel();", replace: "  arm();\n  return () => {};", cases: [U4.watch] },
  { id: "U4-M40", what: "a read answering after navigation is shown", file: RV,
    find: "if (answer !== null && !controller.signal.aborted) show(answer);", replace: "if (answer !== null) show(answer);",
    cases: [U4.reads] },
  { id: "U4-M41", what: "a restore keeps the old content on screen while it re-reads", file: RV,
    find: "      show({ state: \"loading\" });\n", replace: "", cases: [U4.reads] },
  { id: "U4-M42", what: "the result fetch sends credentials cross-origin", file: RV,
    find: "      credentials: \"same-origin\",", replace: "      credentials: \"include\",", cases: [U4.fetch] },
  { id: "U4-M43", what: "an abandoned read shows 'unavailable'", file: RV,
    find: "return signal?.aborted ? null : { state: \"unavailable\" };", replace: "return { state: \"unavailable\" };",
    cases: [U4.fetch] },

  // --- U2: keys ------------------------------------------------------------------------------
  { id: "U2-M01", what: "a failed key read is shown as an empty list ('No keys yet')", file: KEYS_VM,
    find: '  if (keys === null || !keys.ok) return { create, list: { kind: "unavailable", message: LIST_FAILED, retry: true } };',
    replace: '  if (keys === null || !keys.ok) return { create, list: { kind: "empty" } };', cases: [T.kFailed] },
  { id: "U2-M02", what: "a suspended individual is offered key creation (R33)", file: KEYS_VM,
    find: '  const create: KeysModel["create"] = context.account.suspended', replace: '  const create: KeysModel["create"] = false',
    cases: [T.kGate, T.kSuspended] },
  { id: "U2-M03", what: "every not-ready state collapses into one retryable outage", file: KEYS_VM,
    find: "Object.hasOwn(NOT_READY, context.state) ? NOT_READY[context.state] : UNAVAILABLE", replace: "UNAVAILABLE",
    cases: [T.kGate, T.kFailed] },
  { id: "U2-M04", what: "the not-ready table is read off the prototype chain", file: KEYS_VM,
    find: "Object.hasOwn(NOT_READY, context.state) ? NOT_READY[context.state] : UNAVAILABLE",
    replace: "NOT_READY[context.state] ?? UNAVAILABLE", cases: [T.kFailed] },
  { id: "U2-M05", what: "a revoked key offers a second revoke", file: KEYS_VM,
    find: "    revocable: key.revoked_at === null,", replace: "    revocable: true,", cases: [T.kRows] },
  { id: "U2-M06", what: "the row shows the key id where the stored prefix belongs", file: KEYS_VM,
    find: "    prefix: `${key.prefix}…`,", replace: "    prefix: key.id,", cases: [T.kRows] },
  { id: "U2-M07", what: "key times follow the server locale instead of UTC", file: KEYS_VM,
    find: "  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;", replace: '  return new Date(iso).toLocaleString("en-US");',
    cases: [T.kRows] },
  { id: "U2-M08", what: "a replayed creation shows a secret again (R16)", file: KEYS_VM,
    find: '  if (result.value.replayed || result.value.secret === null) return { kind: "notice", message: REPLAYED_COPY };',
    replace: '  if (result.value.secret === null) return { kind: "notice", message: REPLAYED_COPY };', cases: [T.kOnce] },
  { id: "U2-M09", what: "a refused creation hides why behind the replay text", file: KEYS_VM,
    find: '  if (!result.ok) return { kind: "notice", message: result.error.message };',
    replace: '  if (!result.ok) return { kind: "notice", message: REPLAYED_COPY };', cases: [T.kOnce] },
  { id: "U2-M10", what: "the revocation copy stops being P-26's", file: "app/(console)/docs/content.ts",
    find: "Revoking a key stops new requests immediately.", replace: "Revoking a key stops requests within a minute.",
    cases: [T.kCopy] },
  { id: "U2-M11", what: "the confirmation says revocation takes a minute", file: KEYS_VM,
    find: "New requests with this key are refused immediately. This cannot be undone.",
    replace: "Calls using it start failing within a minute.", cases: [T.kCopy] },
  { id: "U2-M12", what: "the plaintext is kept in sessionStorage for later redisplay", file: DIALOG,
    find: '    if (outcome.kind === "secret") setSecret(outcome.secret);',
    replace: '    if (outcome.kind === "secret") { sessionStorage.setItem("infrx:key", outcome.secret); setSecret(outcome.secret); }',
    cases: [T.sSecret] },
  { id: "U2-M13", what: "a reopened dialog reuses the last idempotency key, so a new key replays the old one", file: DIALOG,
    find: "      setAttempt(crypto.randomUUID());\n", replace: "", cases: [T.sActions] },
  { id: "U2-M14", what: "creation is sent without an idempotency key, so a double submit mints twice", file: DIALOG,
    find: "createConsumerKey({ name, idempotency_key: attempt })", replace: "createConsumerKey({ name })", cases: [T.sActions] },

  // Fix round (review 0-U2-V-1..5): the page and control seams a view model cannot see.
  { id: "U2-M23", what: "a failed key read renders 'No keys yet' in the page (review V03b)", file: KEYS_PAGE,
    find: "<p>{model.list.message}</p>", replace: "<p>No keys yet.</p>", cases: [T.sRead] },
  { id: "U2-M24", what: "the plaintext is put in the URL fragment (review V07)", file: DIALOG,
    find: 'if (outcome.kind === "secret") setSecret(outcome.secret);',
    replace: 'if (outcome.kind === "secret") { setSecret(outcome.secret); history.replaceState(null, "", "#" + outcome.secret); }',
    cases: [T.sSecret] },
  { id: "U2-M25", what: "the plaintext is sent over XMLHttpRequest (review V08)", file: DIALOG,
    find: 'if (outcome.kind === "secret") setSecret(outcome.secret);',
    replace: 'if (outcome.kind === "secret") { setSecret(outcome.secret); new XMLHttpRequest().send(outcome.secret); }',
    cases: [T.sSecret] },
  { id: "U2-M26", what: "the plaintext is interpolated into a toast (review V10)", file: DIALOG,
    find: 'toast.success("API key copied");', replace: "toast.success(`API key ${secret} copied`);", cases: [T.sSecret] },
  { id: "U2-M27", what: "a reopened dialog shows the previous key's plaintext again (review V09)", file: DIALOG,
    find: "      setSecret(null);\n", replace: "", cases: [T.sCopy] },
  { id: "U2-M28", what: "the dialog drops the one-time copy (review V24)", file: DIALOG,
    find: "<DialogDescription>{ONE_TIME_COPY}</DialogDescription>", replace: "<DialogDescription>Keep it safe.</DialogDescription>",
    cases: [T.sCopy] },
  { id: "U2-M29", what: "the page drops the lost-key copy (review V25)", file: KEYS_PAGE,
    find: "          <p>{LOST_KEY_COPY}</p>\n", replace: "", cases: [T.sCopy] },
  { id: "U2-M30", what: "the revoke control skips the confirmation (review V11)", file: REVOKE,
    find: "!confirm(revokeConfirmText(name))", replace: "!revokeConfirmText(name)", cases: [T.sActions] },
  { id: "U2-M31", what: "revoke sends the key's name, so every revoke is not_found (review V12)", file: REVOKE,
    find: "revokeConsumerKey(id)", replace: "revokeConsumerKey(name)", cases: [T.sActions] },
  { id: "U2-M32", what: "the list is not re-read after a revoke (review V13)", file: REVOKE,
    find: "    router.refresh();\n", replace: "", cases: [T.sActions] },
  { id: "U2-M33", what: "the dialog asks for trace capture, so C3A refuses every creation (review V14)", file: DIALOG,
    find: "idempotency_key: attempt })", replace: 'idempotency_key: attempt, trace_mode: "on" })', cases: [T.sActions] },
  { id: "U2-M34", what: "a row's revoke control is bound to the key name instead of its id", file: KEYS_PAGE,
    find: "<RevokeButton id={k.id} name={k.name} />", replace: "<RevokeButton id={k.name} name={k.name} />", cases: [T.sActions] },
  // Review 0-U2-V-3: a rejected server action must not leave a spinner and no message.
  { id: "U2-M35", what: "the create call is awaited bare, so a rejection leaves the dialog spinning", file: DIALOG,
    find: "await settle(() => createConsumerKey({ name, idempotency_key: attempt }), CREATE_LOST)",
    replace: "await createConsumerKey({ name, idempotency_key: attempt })", cases: [T.sActions, T.sSecret] },
  { id: "U2-M36", what: "the revoke call is awaited bare, so a rejection skips the refresh", file: REVOKE,
    find: "await settle(() => revokeConsumerKey(id), REVOKE_LOST)", replace: "await revokeConsumerKey(id)", cases: [T.sActions] },
  { id: "U2-M37", what: "settle returns the call un-awaited, so the rejection escapes its try", file: KEYS_VM,
    find: "    return await call();", replace: "    return call();", cases: [T.kLost] },
  { id: "U2-M38", what: "a rejected call shows the raw error text", file: KEYS_VM,
    find: '  } catch {\n    return { ok: false, error: { code: "dependency_unavailable", message: lost } };',
    replace: '  } catch (e) {\n    return { ok: false, error: { code: "dependency_unavailable", message: String(e) } };', cases: [T.kLost] },
  { id: "U2-M39", what: "the create dialog never clears pending", file: DIALOG,
    find: "    setPending(false);\n", replace: "", cases: [T.sActions] },
  { id: "U2-M40", what: "the revoke control never clears pending", file: REVOKE,
    find: "    setPending(false);\n", replace: "", cases: [T.sActions] },

  // --- U2: settings --------------------------------------------------------------------------
  { id: "U2-M15", what: "consumer trace capture is presented as on", file: SETTINGS_VM,
    find: '    status: "Off",', replace: '    status: "On" as "Off",', cases: [T.pFacts] },
  { id: "U2-M16", what: "the page claims zero data retention", file: SETTINGS_VM,
    find: "This is not zero data retention.", replace: "We keep zero data retention.", cases: [T.pTruthful] },
  { id: "U2-M17", what: "the retention row stops pointing at the published periods", file: SETTINGS_VM,
    find: '    href: "/docs#retention",', replace: "    href: null,", cases: [T.pTruthful] },
  { id: "U2-M18", what: "trace-off is presented as deleting what serving stores", file: SETTINGS_VM,
    find: "It does not change what we store to run a request (above).", replace: "Turning it off deletes your data.",
    cases: [T.pTruthful] },
  { id: "U2-M19", what: "signup is presented as granting data-use permission", file: SETTINGS_VM,
    find: "Signing up grants no permission for any of these", replace: "Signing up grants these permissions", cases: [T.pConsent] },
  { id: "U2-M20", what: "a failed account load renders as a blank verified account", file: SETTINGS_VM,
    find: '  return { account: { kind: "unavailable", message: "Your account could not be loaded right now. Reload the page to try again." }, privacy };',
    replace: '  return { account: { kind: "ready", email: "", status: "Verified", suspended: false }, privacy };', cases: [T.pAccount] },
  { id: "U2-M21", what: "a suspended account is shown as in good standing", file: SETTINGS_VM,
    find: 'status: "Verified", suspended: context.account.suspended }', replace: 'status: "Verified", suspended: false }',
    cases: [T.pAccount] },
  { id: "U2-M22", what: "a privacy fact is rendered as a checkbox that saves nothing", file: SETTINGS_PAGE,
    find: '<Badge variant="outline">{row.status}</Badge>',
    replace: '<input type="checkbox" defaultChecked={row.status === "Off"} aria-label={row.title} />', cases: [T.sSettings] },
  // --- U3: operator console ---------------------------------------------------------------------
  { id: "U3-M01", what: "the form-side actor is sent to the database", file: O_PORT,
    find: "const audited = { p_reason: command.reason, p_idempotency_key: command.idempotency_key };",
    replace: "const audited = { p_reason: command.reason, p_idempotency_key: command.idempotency_key, p_actor: command.actor };",
    cases: [O.rpc] },
  { id: "U3-M02", what: "a replay is reported as a fresh change", file: O_PORT,
    find: "return { ok: true, value: { replayed } };", replace: "return { ok: true, value: { replayed: false } };", cases: [O.replay] },
  { id: "U3-M03", what: "an unreadable answer is taken as success", file: O_PORT,
    find: "if (Array.isArray(data) || typeof replayed !== \"boolean\") ", replace: "if (Array.isArray(data)) ", cases: [O.replay] },
  { id: "U3-M04", what: "the signup grant is sent as an operator change", file: O_PORT,
    find: "if (target === null) return fail(", replace: "if (target === null && false) return fail(", cases: [O.grant] },
  { id: "U3-M05", what: "a database authority refusal reads as 'not confirmed'", file: O_PORT,
    find: "  if (error.code === \"42501\") return fail(...(REFUSALS.forbidden as [ErrorCode, string]));\n", replace: "", cases: [O.refusals] },
  { id: "U3-M06", what: "the database's refusal text and any code reach the page", file: O_PORT,
    find: "if (known !== undefined) return fail(...known);",
    replace: "return fail((/^([a-z_]+):/.exec(error.message ?? \"\")?.[1] ?? \"internal_error\") as ErrorCode, error.message ?? \"\");",
    cases: [O.refusals] },
  { id: "U3-M07", what: "a transport failure is reported as a committed change", file: O_PORT,
    find: "      } catch {\n        return fail(\"dependency_unavailable\", UNCONFIRMED);",
    replace: "      } catch {\n        return { ok: true, value: { replayed: false } };", cases: [O.transport] },
  { id: "U3-M08", what: "a wallet whose figures do not reconcile is shown", file: O_READS,
    find: "    if (subCredit(ledgerTotal, reservedTotal) !== available) throw new Malformed(\"available\");\n", replace: "",
    cases: [O.malformed] },
  { id: "U3-M09", what: "an organization's suspension is dropped from the account row", file: O_READS,
    find: "      suspended: org.suspended,", replace: "      suspended: false,", cases: [O.accounts] },
  { id: "U3-M10", what: "one failed section takes another down", file: O_READS,
    find: "section(() => audit(client)),", replace: "section(() => accounts(client).then(() => audit(client))),", cases: [O.malformed] },
  { id: "U3-M11", what: "a JSON number is accepted as money", file: O_READS,
    find: "    return parseCredit(row[key]);", replace: "    return parseCredit(String(row[key]));", cases: [O.malformed, O.audit] },
  { id: "U3-M12", what: "an audit action outside the closed vocabulary is shown", file: O_READS,
    find: "    if (!(AUDIT_ACTIONS as readonly string[]).includes(action)) throw new Malformed(\"action\");\n", replace: "",
    cases: [O.audit] },
  { id: "U3-M13", what: "a legacy USD hold is labelled CREDIT", file: O_READS,
    find: ": { amount: parseUsd(r.hold), unit: \"USD\" };", replace: ": { amount: parseCredit(r.hold), unit: \"CREDIT\" };", cases: [O.units] },
  { id: "U3-M14", what: "the audit read is unbounded", file: O_READS,
    find: "      .order(\"at\", { ascending: false })\n      .limit(AUDIT_LIMIT),", replace: "      .order(\"at\", { ascending: false }),",
    cases: [O.surface] },
  { id: "U3-M15", what: "provider_dev wallets are listed as consumer accounts", file: O_READS,
    find: "      .eq(\"kind\", \"consumer\")\n", replace: "", cases: [O.surface] },
  { id: "U3-M16", what: "a failed submission rotates the idempotency key (a retry can apply twice)", file: O_FORM,
    find: "return result.ok ? fresh() : current;", replace: "return fresh();", cases: [O.key] },
  { id: "U3-M17", what: "a replay is announced as a new commit", file: O_FORM,
    find: "    return result.value.replayed\n", replace: "    return !result.value.replayed\n", cases: [O.outcome] },
  { id: "U3-M18", what: "the suspension status is submitted as text", file: O_FORM,
    find: "input[field.name] = field.kind === \"suspended\" ? value === \"true\" : value;", replace: "input[field.name] = value;",
    cases: [O.input] },
  { id: "U3-M19", what: "the page renders for a non-operator", file: O_PAGE,
    find: "  if (!session.isOperator) notFound();\n", replace: "", cases: [O.gate] },
  { id: "U3-M20", what: "the page reads with the service key instead of the operator's session", file: O_PAGE,
    find: "operatorReads((await createClient()) as unknown as ReadClient)", replace: "operatorReads(createAdminClient() as unknown as ReadClient)",
    cases: [O.gate, O.noEdit] },
  { id: "U3-M21", what: "the form rotates its key after every answer (a 'not confirmed' retry applies twice)", file: O_FORMS,
    find: "setKey(nextKey(key, result, newKey));", replace: "setKey(newKey());", cases: [O.wiring] },
  { id: "U3-M22", what: "the form submits a fresh key instead of the one it holds", file: O_FORMS,
    find: "String(data.get(name)) : null), key);", replace: "String(data.get(name)) : null), newKey());", cases: [O.wiring] },
  { id: "U3-M23", what: "a failed section renders its empty table ('everything reconciles')", file: O_PAGE,
    find: "  if (!result.ok) {\n    return (\n      <p role=\"status\" className=\"p-4 text-sm text-destructive\">\n        Unavailable: {result.error.message}.\n      </p>\n    );\n  }\n",
    replace: "  if (!result.ok) return <>{children([] as T)}</>;\n", cases: [O.sections] },
  { id: "U3-M24", what: "a failed drift read bypasses Section and shows as no drift", file: O_PAGE,
    find: "<Section result={view.drift}>", replace: "<Section result={view.drift.ok ? view.drift : { ok: true, value: [] }}>",
    cases: [O.sections] },
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
  // A test FILE that failed to load is reported under its path: that is a broken copy, not a kill.
  if (run.failed.some((entry) => /\.test\.ts$/.test(entry.name))) {
    return { outcome: "runner-error", why: "a test file did not load" };
  }
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
if (selected.length === 0) {
  // A filter that matches nothing used to print "0 mutants, 0 killed" and exit 0, which is a green
  // run that tested nothing — the same class of lie as a surviving mutant.
  console.error(`--only ${only} matched no mutant of ${MUTANTS.length}`);
  process.exit(1);
}
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
