// UX-08 (L-08): the Evaluations pages' lane-local view model. It wraps B4's record copy
// (lib/services/evaluation/view.ts) and adds only what the design asks: a catalog that could not be
// read is "unavailable", never an empty form; no report yet is "Running"/"Awaiting comparison",
// never a score; a decision is scoped to its protocol; and the launch copy invents no threshold.
import type { Role } from "@/lib/auth/access";
import type { Catalog, Experiment, Report, Result } from "@/lib/services/evaluation/port";
import { comparison, runRow } from "@/lib/services/evaluation/view";
import type { Tone } from "@/components/ui/badge";

const WHAT: Record<keyof Catalog, string> = { datasets: "frozen dataset", harnesses: "harness revision", evaluators: "evaluator", servings: "serving revision" };
export const LAUNCH_NEEDS: (keyof Catalog)[] = ["datasets", "harnesses", "evaluators", "servings"];
export const SUBSCRIBE_NEEDS: (keyof Catalog)[] = ["datasets", "harnesses", "evaluators"];

export type CatalogState = { kind: "unavailable" } | { kind: "empty"; missing: string[] } | { kind: "ready"; catalog: Catalog };

/** Any refusal of the listing is "could not read", not "nothing offered" (SR-AP10-1's 503 today). */
export function catalogState(catalog: Result<Catalog>, needs: (keyof Catalog)[]): CatalogState {
  if (!catalog.ok) return { kind: "unavailable" };
  const missing = needs.filter((k) => catalog.value[k].length === 0).map((k) => WHAT[k]);
  return missing.length > 0 ? { kind: "empty", missing } : { kind: "ready", catalog: catalog.value };
}

const TONE: Record<Report["decision"]["outcome"], Tone> = { accept: "info", reject: "warning", inconclusive: "neutral" };
export type Decision = { tone: Tone; text: string; scope: string | null };

const decided = (r: Report): Decision =>
  ({ tone: TONE[r.decision.outcome], text: comparison(r).outcome, scope: "under this protocol only, not a general quality certification" });

/** B2's outcome as its own copy says it; before a report exists, what the runs are doing. */
export function decision(e: Experiment): Decision {
  if (e.report !== null) return decided(e.report);
  const active = [e.baseline, e.candidate].some((r) => r.state === "queued" || r.state === "running");
  return { tone: "neutral", text: active ? "Running: no result yet" : "Awaiting comparison", scope: null };
}

export function experimentRow(role: Role, e: Experiment) {
  return {
    id: e.experiment_id, created: e.created_at,
    baseline: runRow(role, e.baseline).state, candidate: runRow(role, e.candidate).state,
    decision: decision(e), dataset: e.baseline.dataset_ref,
  };
}

/** The comparison decision-first: B4's rows plus the caveats L-08 requires next to them. */
export function reportView(r: Report) {
  return {
    ...comparison(r),
    decision: decided(r),
    basisNote: r.estimates.basis === "teacher_judgment" ? "Scored by teacher judgment: a model's judgment is a source of evidence, not ground truth." : null,
    latencyNote: "Latency is as each run observed it. The report records no workload or hardware identity, so it is not a performance comparison.",
  };
}

/** Explanations only: every number in the protocol is the expert's (no platform default). */
export const PROTOCOL_HELP: Record<string, string> = {
  metric_source: "What scores each case: the evaluator's deterministic metric, or a teacher model's judgment (set up under Judge setup).",
  confidence: "The confidence level of every interval, as a fraction between one half and one.",
  margin: "How far the candidate may fall below the baseline and still count as non-inferior, in the metric's own units.",
  min_cases: "Paired cases needed before the overall comparison may decide anything; fewer is inconclusive, not a failure.",
  slices: "Slices that must each hold on their own: one per line as name, margin and minimum cases.",
  seed: "Fixes which cases are drawn, so the baseline and the candidate see the same ones.",
  max_cases: "The most cases each run evaluates.",
};

export const LAUNCH_COPY = {
  limit: "Each of the two runs may spend up to this limit in provider_dev CREDIT, so the experiment may spend up to twice it.",
  serving: "The report states what differs between the two revisions; nothing here assumes a single-factor change. Both are pinned revisions.",
};
