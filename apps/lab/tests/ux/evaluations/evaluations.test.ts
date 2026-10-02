// UX-08 (L-08, UX-T11): the Evaluations hub's view model and its pages. Records are B4's port types and
// B2's real reports (tests/b/real); variants are derived from them. Each case names what it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import type { Catalog, Experiment, Report, Result, Run } from "../../../lib/services/evaluation/port.ts";
import { comparison } from "../../../lib/services/evaluation/view.ts";
import { REPORTS, RUNS } from "../../b/real.ts";
import "./alias.ts";

const { catalogState, decision, experimentRow, LAUNCH_COPY, PROTOCOL_HELP, reportView } = await import("../../../app/(provider)/evaluations/view.ts");

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const HUB = "app/(provider)/evaluations/page.tsx";
const CHECKPOINTS = "app/(provider)/evaluations/checkpoints/page.tsx";
const REPORT = "app/(provider)/evaluations/report.tsx";

const opt = (kind: string) => ({ ref: `lab:${kind}:a@sha256:${"0".repeat(64)}`, label: `${kind} one` });
const CATALOG: Catalog = {
  datasets: [opt("dataset")], evaluators: [opt("evaluator")], servings: [opt("serving")],
  harnesses: [{ ref: `lab:harness:a@sha256:${"1".repeat(64)}`, harness_id: "sop", version: 3, adapter: "finite_video" }],
};
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const run = (state: Run["state"]): Run => ({ ...structuredClone(RUNS.queued), state });
const experiment = (baseline: Run, candidate: Run, report: Report | null): Experiment =>
  ({ experiment_id: "e1", created_at: "2026-10-02T00:00:00Z", protocol: REPORTS.reject.protocol, baseline, candidate, report });
const accept = (): Report => ({ ...structuredClone(REPORTS.reject), decision: { outcome: "accept", reasons: [] } });

test("UX08-C01 a catalog the service could not read is unavailable, never an empty launch form", () => {
  assert.deepEqual(catalogState({ ok: false, reason: "unavailable" }, ["datasets"]), { kind: "unavailable" });
  assert.deepEqual(catalogState({ ok: false, reason: "denied" }, ["datasets"]), { kind: "unavailable" }, "any refusal of the listing is not 'nothing offered'");
  assert.deepEqual(catalogState(ok(CATALOG), ["datasets", "harnesses", "evaluators", "servings"]), { kind: "ready", catalog: CATALOG });
});

test("UX08-C02 a catalog that answers but lacks an input names exactly what is missing, for the form that needs it", () => {
  const none = { ...CATALOG, servings: [], evaluators: [] };
  assert.deepEqual(catalogState(ok(none), ["datasets", "harnesses", "evaluators", "servings"]), { kind: "empty", missing: ["evaluator", "serving revision"] });
  assert.deepEqual(catalogState(ok({ ...CATALOG, servings: [] }), ["datasets", "harnesses", "evaluators"]).kind, "ready", "a subscription needs no serving revision");
});

test("UX08-D01 no report yet is Running or Awaiting comparison, never a score", () => {
  assert.deepEqual(decision(experiment(run("running"), run("queued"), null)), { tone: "neutral", text: "Running: no result yet", scope: null });
  assert.deepEqual(decision(experiment(run("succeeded"), run("running"), null)).text, "Running: no result yet", "one arm still running");
  assert.deepEqual(decision(experiment(run("succeeded"), run("cancelled"), null)), { tone: "neutral", text: "Awaiting comparison", scope: null });
});

test("UX08-D02 a stored report's decision is B2's own outcome, scoped to its protocol, with a tone that never certifies", () => {
  const scope = "under this protocol only, not a general quality certification";
  for (const [report, tone] of [[accept(), "info"], [REPORTS.reject, "warning"], [REPORTS.inconclusive, "neutral"]] as const) {
    assert.deepEqual(decision(experiment(run("succeeded"), run("succeeded"), report)), { tone, text: comparison(report).outcome, scope }, report.decision.outcome);
  }
});

test("UX08-R01 an experiment row: the identity, both arms' states, the frozen dataset and the decision, in that order", () => {
  const e = experiment(run("succeeded"), run("cancelled"), REPORTS.inconclusive);
  const row = experimentRow("viewer", e);
  assert.deepEqual(Object.keys(row), ["id", "created", "baseline", "candidate", "decision", "dataset"]);
  assert.deepEqual([row.id, row.created, row.baseline, row.candidate, row.dataset], ["e1", e.created_at, "finished", "cancelled", RUNS.queued.dataset_ref]);
});

test("UX08-T01 the report leads with the decision and its reasons, keeps every count and slice, and labels insufficiency apart from inferiority", () => {
  const r = structuredClone(REPORTS.inconclusive);
  r.estimates.slices.small = { n: 1, clusters: 1, diff: null, low: null, high: null, verdict: "insufficient", improved: false };
  r.protocol.required_slices.small = { margin: 0.1, min_cases: 5 };
  const v = reportView(r);
  assert.equal(v.decision.text, comparison(r).outcome);
  assert.deepEqual(v.reasons, ["coverage 38/40", "overall uncertain"]);
  assert.deepEqual(v.estimates.map((e) => [e.name, e.verdict]), [["overall", "uncertain"], ["slice safety", "non-inferior"], ["slice small", "too few cases to decide"]]);
  assert.deepEqual(v.runs.map((x) => [x.missing, x.errors, x.notComparable]), [["0 / 40 missing", "0 errored", "0 not comparable"], ["1 / 40 missing", "1 errored", "1 not comparable"]]);
  assert.equal(v.paired, "38 / 40 cases paired");
});

test("UX08-T02 mixed units stay one line per unit, and latency is labelled as no performance comparison", () => {
  const v = reportView(REPORTS.inconclusive);
  assert.deepEqual(v.runs[1].costs, ["39.00000000 CREDIT", "0.25000000 PROVIDER_USD"]);
  assert.match(v.latencyNote, /not a performance comparison/);
  assert.equal(v.basisNote, null, "a deterministic metric needs no source caveat");
  const teacher = structuredClone(REPORTS.inconclusive);
  teacher.estimates.basis = "teacher_judgment";
  assert.match(reportView(teacher).basisNote ?? "", /source of evidence, not ground truth/);
});

test("UX08-T03 the launch copy invents no threshold: protocol help carries no number and the limit is stated per run", () => {
  for (const [name, help] of Object.entries(PROTOCOL_HELP)) assert.doesNotMatch(help, /\d/, name);
  assert.deepEqual(Object.keys(PROTOCOL_HELP).sort(), ["confidence", "margin", "max_cases", "metric_source", "min_cases", "seed", "slices"]);
  assert.match(LAUNCH_COPY.limit, /each of the two runs/i);
  assert.match(LAUNCH_COPY.limit, /twice/);
});

test("UX08-P01 the hub reads the catalog only through catalogState: unavailable and empty render as service states, the form only when ready", () => {
  const page = read(HUB);
  assert.match(page, /const launch = catalogState\(catalog, LAUNCH_NEEDS\);/);
  assert.match(page, /launch\.kind === "unavailable" \? \(\n\s+<ServiceState state="unavailable"/);
  assert.match(page, /launch\.kind === "empty" \? \(\n\s+<ServiceState state="empty"/);
  assert.match(page, /if \(!runs\.ok \|\| !experiments\.ok\) return <p role="alert">\{REFUSAL_COPY\.unavailable\}<\/p>;/, "only the records themselves fail the page");
  assert.doesNotMatch(page, /!catalog\.ok/, "a catalog failure never hides the records");
  const sub = read(CHECKPOINTS);
  assert.match(sub, /const offered = catalogState\(catalog, SUBSCRIBE_NEEDS\);/);
  assert.match(sub, /if \(!subscriptions\.ok\) return <p role="alert">\{REFUSAL_COPY\.unavailable\}<\/p>;/);
  assert.match(sub, /offered\.kind === "unavailable" \? \(\n\s+<ServiceState state="unavailable"/);
});

test("UX08-P02 the protocol and limits are typed by the expert: no default value, and the metric basis starts unchosen", () => {
  const page = read(HUB);
  for (const name of ["seed", "max_cases", "run_limit", "confidence", "margin", "min_cases"]) {
    const input = new RegExp(`<input[^>]*name="${name}"[^>]*>`).exec(page)?.[0] ?? "";
    assert.ok(input.includes("required"), name);
    assert.doesNotMatch(input, /defaultValue|value=|placeholder/, name);
  }
  assert.match(page, /<select name="metric_source" required[^>]*>\s*<option value="">Choose the metric basis<\/option>/);
});

test("UX08-P03 readable selectors: every catalog choice shows its label, and its immutable ref is listed beside it", () => {
  const page = read(HUB);
  assert.match(page, /<RefList catalog=\{launch\.catalog\} \/>/);
  assert.match(read("app/(provider)/evaluations/refs.tsx"), /<code className="lab-id">\{o\.ref\}<\/code>/);
  assert.doesNotMatch(page, /latest/i, "no mutable 'latest' choice");
});

test("UX08-P04 the report component leads with the decision and keeps the protocol scope next to it", () => {
  const report = read(REPORT);
  const decided = report.indexOf("{v.decision.text}");
  assert.ok(decided !== -1 && decided < report.indexOf("{v.estimates.map"), "decision before the estimates");
  assert.match(report, /<Badge tone=\{v\.decision\.tone\}>\{v\.decision\.text\}<\/Badge> \{v\.decision\.scope\}/);
});

test("UX08-N01 Evaluations lists Experiments, Runs, Checkpoint subscriptions and Judge setup, marking the current one", () => {
  const nav = read("app/(provider)/evaluations/nav.tsx");
  for (const href of ["/evaluations#experiments", "/evaluations#runs", "/evaluations/checkpoints", "/judge"]) assert.ok(nav.includes(`"${href}"`), href);
  assert.match(nav, /aria-current=\{current === t\.id \? "page" : undefined\}/);
  assert.match(read(HUB), /<EvaluationsNav current="experiments" \/>/);
  assert.match(read(CHECKPOINTS), /<EvaluationsNav current="checkpoints" \/>/);
});
