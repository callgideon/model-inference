#!/usr/bin/env node
// UX-08's mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): each Evaluations /
// Judge setup decision is one edit that a UX08 case it names must fail by assertion. Unit suites only.
// Usage: node tests/ux/evaluations/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/ux/evaluations/evaluations.test.ts", "tests/ux/evaluations/judge.test.ts", "tests/ux/evaluations/records.test.ts"];
const E = "app/(provider)/evaluations";
const VIEW = `${E}/view.ts`;
const HUB = `${E}/page.tsx`;
const SUBS = `${E}/checkpoints/page.tsx`;
const J = "app/(provider)/judge";
const JV = `${J}/view.ts`;
const JP = `${J}/page.tsx`;
const JF = `${J}/form.tsx`;
const JR = "lib/services/judge/records.ts";

const C = {
  c01: "UX08-C01 a catalog the service could not read is unavailable, never an empty launch form",
  c02: "UX08-C02 a catalog that answers but lacks an input names exactly what is missing, for the form that needs it",
  d01: "UX08-D01 no report yet is Running or Awaiting comparison, never a score",
  d02: "UX08-D02 a stored report's decision is B2's own outcome, scoped to its protocol, with a tone that never certifies",
  r01: "UX08-R01 an experiment row: the identity, both arms' states, the frozen dataset and the decision, in that order",
  t01: "UX08-T01 the report leads with the decision and its reasons, keeps every count and slice, and labels insufficiency apart from inferiority",
  t02: "UX08-T02 mixed units stay one line per unit, and latency is labelled as no performance comparison",
  t03: "UX08-T03 the launch copy invents no threshold: protocol help carries no number and the limit is stated per run",
  p01: "UX08-P01 the hub reads the catalog only through catalogState: unavailable and empty render as service states, the form only when ready",
  p02: "UX08-P02 the protocol and limits are typed by the expert: no default value, and the metric basis starts unchosen",
  p03: "UX08-P03 readable selectors: every catalog choice shows its label, and its immutable ref is listed beside it",
  p04: "UX08-P04 the report component leads with the decision and keeps the protocol scope next to it",
  n01: "UX08-N01 Evaluations lists Experiments, Runs, Checkpoint subscriptions and Judge setup, marking the current one",
  j01: "UX08-J01 the view reads only fields the committed judge API documents, and names every run state it can return",
  j02: "UX08-J02 calibrated is said only for a calibrated state; too few reference labels says how many of how many",
  j03: "UX08-J03 a judge run shows the backend's state and counts; an ambiguous send is unknown, never retried or failed",
  j04: "UX08-J04 a budget shows limit, reserved and settled each with its own unit, never a sum",
  j05: "UX08-J05 judge setup sits under Evaluations and says unreadable records are unavailable, not absent",
  j06: "UX08-J06 configure and budget send a key minted at render; only an unknown outcome keeps it for the retry, a definite answer frees it",
  j07: "UX08-J07 each judge form carries every field its action reads, so a filled form is sent, never refused as invalid",
  k01: "UX08-K01 a workspace's configurations, runs and budgets are read for the guarded workspace only, as the routes answer them",
  k02: "UX08-K02 a misconfigured Lab is unavailable before any call",
  k03: "UX08-K03 any refusal or lost answer of any one list is unavailable, never the other lists alone",
  k04: "UX08-K04 an answer it cannot read whole is unavailable: never CREDIT as PROVIDER_USD, never an unknown state, never a partial list",
  k05: "UX08-K05 a list with a further page is shown with more set, so the page never reads it as complete",
};

const MUTANTS = [
  m("UX08-X01", "an unreadable catalog reads as nothing offered", VIEW, 'if (!catalog.ok) return { kind: "unavailable" };', 'if (!catalog.ok) return { kind: "empty", missing: [] };', [C.c01]),
  m("UX08-X02", "a catalog missing an input is offered as ready", VIEW, "return missing.length > 0 ?", "return false ?", [C.c02]),
  m("UX08-X03", "a subscription demands a serving revision", VIEW, 'SUBSCRIBE_NEEDS: (keyof Catalog)[] = ["datasets", "harnesses", "evaluators"];', 'SUBSCRIBE_NEEDS: (keyof Catalog)[] = ["datasets", "harnesses", "evaluators", "servings"];', [C.c02]),
  m("UX08-X04", "ended runs without a report still say Running", VIEW, 'active ? "Running: no result yet" : "Awaiting comparison"', '"Running: no result yet"', [C.d01]),
  m("UX08-X05", "a queued arm counts as ended", VIEW, '(r) => r.state === "queued" || r.state === "running"', '(r) => r.state === "running"', [C.d01]),
  m("UX08-X06", "an accept is shown as certified success", VIEW, 'accept: "info"', 'accept: "success"', [C.d02]),
  m("UX08-X07", "the decision loses its protocol scope", VIEW, 'scope: "under this protocol only, not a general quality certification" });', "scope: null });", [C.d02]),
  m("UX08-X08", "the decision is re-worded from the raw outcome", VIEW, "text: comparison(r).outcome,", "text: r.decision.outcome,", [C.d02]),
  m("UX08-X09", "the row moves the dataset before the decision", VIEW, "decision: decision(e), dataset: e.baseline.dataset_ref,", "dataset: e.baseline.dataset_ref, decision: decision(e),", [C.r01]),
  m("UX08-X10", "the candidate column shows the baseline", VIEW, "candidate: runRow(role, e.candidate).state", "candidate: runRow(role, e.baseline).state", [C.r01]),
  m("UX08-X11", "the report's decision is invented", VIEW, "decision: decided(r),", 'decision: { tone: "neutral" as const, text: "Inconclusive", scope: null },', [C.t01]),
  m("UX08-X12", "teacher judgment loses its source caveat", VIEW, 'r.estimates.basis === "teacher_judgment" ?', "false ?", [C.t02]),
  m("UX08-X13", "latency is presented as a performance comparison", VIEW, "so it is not a performance comparison.", "so it compares performance.", [C.t02]),
  m("UX08-X14", "the margin help suggests a threshold", VIEW, "in the metric's own units.", "in the metric's own units; 0.05 is typical.", [C.t03]),
  m("UX08-X15", "the limit is described as the experiment's total", VIEW, "Each of the two runs may spend up to this limit in provider_dev CREDIT, so the experiment may spend up to twice it.", "The experiment may spend up to this limit in provider_dev CREDIT.", [C.t03]),
  m("UX08-X16", "a catalog failure hides the records", HUB, "if (!runs.ok || !experiments.ok) return", "if (!runs.ok || !experiments.ok || !catalog.ok) return", [C.p01]),
  m("UX08-X17", "an unreadable catalog is announced as empty", HUB, '<ServiceState state="unavailable" title="Comparisons', '<ServiceState state="empty" title="Comparisons', [C.p01]),
  m("UX08-X18", "a catalog failure hides the subscriptions", SUBS, "if (!subscriptions.ok) return", "if (!subscriptions.ok || !catalog.ok) return", [C.p01]),
  m("UX08-X19", "the metric basis is preselected", HUB, '<option value="">Choose the metric basis</option>\n', "", [C.p02]),
  m("UX08-X20", "the confidence carries a platform default", HUB, '<input name="confidence" inputMode="decimal" required', '<input name="confidence" inputMode="decimal" defaultValue="0.95" required', [C.p02]),
  m("UX08-X21", "the immutable refs are not listed", HUB, "<RefList catalog={launch.catalog} />", "", [C.p03]),
  m("UX08-X22", "the ref list shows labels twice, no ref", `${E}/refs.tsx`, '<code className="lab-id">{o.ref}</code>', '<code className="lab-id">{o.label}</code>', [C.p03]),
  m("UX08-X23", "the report decision drops its scope", `${E}/report.tsx`, "{v.decision.text}</Badge> {v.decision.scope}</p>", "{v.decision.text}</Badge></p>", [C.p04]),
  m("UX08-X24", "judge setup is not found from Evaluations", `${E}/nav.tsx`, '  { id: "judge", href: "/judge", label: "Judge setup" },\n', "", [C.n01]),
  m("UX08-X25", "the current section is not marked", `${E}/nav.tsx`, 'aria-current={current === t.id ? "page" : undefined}', "aria-current={undefined}", [C.n01]),
  m("UX08-X26", "the subscriptions page loses the section nav", SUBS, '<EvaluationsNav current="checkpoints" />', "", [C.n01]),
  m("UX08-X27", "an API run state has no copy", JV, '  ambiguous: "outcome unknown: not resent automatically",\n', "", [C.j01, C.j03]),
  m("UX08-X28", "an ambiguous send reads as failed", JV, 'ambiguous: "outcome unknown: not resent automatically"', 'ambiguous: "failed"', [C.j03]),
  m("UX08-X29", "enough labels alone is called calibrated", JV, 'if (c.state === "calibrated")', "if (c.labels >= c.required)", [C.j02]),
  m("UX08-X30", "the configuration row shows the raw state", JV, "calibration: calibration(c.calibration)", "calibration: c.calibration.state", [C.j02]),
  m("UX08-X31", "an ended run still says cancel requested", JV, "r.cancel_requested && !ENDED.has(r.domain_state)", "r.cancel_requested", [C.j03]),
  m("UX08-X32", "a requested cancel is not shown", JV, "const cancelling = r.cancel_requested &&", "const cancelling = false &&", [C.j03]),
  m("UX08-X33", "an unsettled amount reads as zero", JV, '(m === null ? "none"', '(m === null ? "0"', [C.j03]),
  m("UX08-X34", "rejected samples are dropped", JV, " · ${r.rejected} rejected", "", [C.j03]),
  m("UX08-X35", "the reserved column shows the limit", JV, "reserved: money(b.reserved)", "reserved: money(b.limit)", [C.j04]),
  m("UX08-X36", "unreadable judge records read as empty", `${J}/records.tsx`, '<ServiceState state="unavailable" title="Judge records', '<ServiceState state="empty" title="Judge records', [C.j05]),
  m("UX08-X37", "the page claims empty records it never read", `${J}/page.tsx`, "<JudgeRecords records={records} />", "<JudgeRecords records={records ?? { configs: [], runs: [], budgets: [], more: false }} />", [C.j05]),
  m("UX08-X38", "judge setup is not placed under Evaluations", `${J}/page.tsx`, 'breadcrumb={[{ href: "/evaluations", label: "Evaluations" }]}', "breadcrumb={[]}", [C.j05]),
  // Register row 98: the judge forms send what their actions read.
  m("UX08-X39", "every refusal keeps the key", JV, '!o.ok && o.reason === "unavailable"', "!o.ok", [C.j06]),
  m("UX08-X40", "a conflict keeps the key, an unknown outcome frees it", JV, 'o.reason === "unavailable"', 'o.reason === "conflict"', [C.j06]),
  m("UX08-X41", "the form replaces the key only when it should keep it", JF, "if (keyed && !retryKeepsKey(outcome))", "if (keyed && retryKeepsKey(outcome))", [C.j06]),
  m("UX08-X42", "the form sends the render's key forever", JF, "Object.entries(values).map(", "Object.entries(hidden).map(", [C.j06]),
  m("UX08-X43", "configure carries no key", JP, '        hidden={{ idempotency_key: mint() }}\n        keyed="idempotency_key"\n      />', "      />", [C.j06, C.j07]),
  m("UX08-X44", "the budget carries no key", JP, '          hidden={{ idempotency_key: mint() }}\n          keyed="idempotency_key"\n        />', "        />", [C.j06, C.j07]),
  m("UX08-X45", "calibration names no configuration", JP, 'fields={[{ name: "config_id", label: "Configuration id" }]}', "fields={[]}", [C.j07]),
  m("UX08-X46", "the run form replaces its run id", JP, "hidden={{ run_id: runId }}", 'hidden={{ run_id: runId }}\n        keyed="run_id"', [C.j06]),
  m("UX08-X47", "the configure key is a fixed field, not minted", JP, '        hidden={{ idempotency_key: mint() }}\n        keyed="idempotency_key"\n      />', '        hidden={{ idempotency_key: runId }}\n        keyed="idempotency_key"\n      />', [C.j06]),
  // WR-UX08-3: the records the page reads from the judge API.
  m("UX08-X48", "the page still lists no records", JP, "<JudgeRecords records={records} />", "<JudgeRecords records={null} />", [C.j05]),
  m("UX08-X49", "a further page goes unmentioned on the page", `${J}/records.tsx`, "      {records.more && <p>", "      {false && <p>", [C.j05]),
  m("UX08-X50", "a misconfigured Lab reads as empty records", JR, "if (api === null) return null;", "if (api === null) return { configs: [], runs: [], budgets: [], more: false };", [C.k02]),
  m("UX08-X51", "a refused list reads as empty records", JR, "if (!configs.ok || !runs.ok || !budgets.ok) return null;", "if (!configs.ok || !runs.ok || !budgets.ok) return { configs: [], runs: [], budgets: [], more: false };", [C.k03]),
  m("UX08-X52", "configurations are read at the route's default page", JR, '"/lab/v1/judge/configs", { query: { ...query, limit: 100 } }', '"/lab/v1/judge/configs", { query }', [C.k01]),
  m("UX08-X53", "runs are read at the route's default page", JR, '"/lab/v1/judge/runs", { query: { ...query, limit: 100 } }', '"/lab/v1/judge/runs", { query }', [C.k01]),
  m("UX08-X54", "CREDIT reads as PROVIDER_USD", JR, '(v as Row).unit === "PROVIDER_USD" && ', "", [C.k04]),
  m("UX08-X55", "an inexact amount is shown", JR, "USD_RE.test((v as Row).amount as string)", "true", [C.k04]),
  m("UX08-X56", "an unknown run state is shown", JR, "domain_state: one(r.domain_state, RUN_STATES)", 'domain_state: r.domain_state as JudgeRun["domain_state"]', [C.k04]),
  m("UX08-X57", "an unknown calibration state is shown", JR, "state: one(c.state, CALIBRATION)", 'state: c.state as Calibration["state"]', [C.k04]),
  m("UX08-X58", "a further page reads as complete", JR, "more: Boolean(configs.data.next_cursor || runs.data.next_cursor)", "more: false", [C.k05]),
  m("UX08-X59", "a further runs page is ignored", JR, "configs.data.next_cursor || runs.data.next_cursor", "configs.data.next_cursor", [C.k05]),
  m("UX08-X60", "an absent amount reads as zero", JR, "reserved: opt(r.reserved, usd)", 'reserved: opt(r.reserved, usd) ?? { amount: "0.00000000", unit: "PROVIDER_USD" }', [C.k01]),
  m("UX08-X61", "a budget without settled is shown", JR, "settled: usd(r.settled) })", "settled: opt(r.settled, usd)! })", [C.k04]),
  m("UX08-X62", "a fractional count is read", JR, "const int = (v: unknown) => (Number.isInteger(v)", 'const int = (v: unknown) => (typeof v === "number"', [C.k04]),
  m("UX08-X63", "a text flag is read as a flag", JR, 'const bool = (v: unknown) => (typeof v === "boolean" ? v : bad());', "const bool = (v: unknown) => Boolean(v);", [C.k04]),
  m("UX08-X64", "an interval of any length is read", JR, "Array.isArray(v) && v.length === 2 && ", "Array.isArray(v) && ", [C.k04]),
  m("UX08-X65", "the budget list is not read", JR, 'budgets: rows(budgets.data).map(budget),', "budgets: [],", [C.k01]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "UX08", mutants: MUTANTS }));
