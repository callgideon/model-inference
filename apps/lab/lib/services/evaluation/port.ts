// B4: the Lab's view of the evaluation backends - B1 runs (D7 `lab_run_status`), B2 reports, B3
// checkpoint subscriptions, H1 harness revisions - through lab-api's LAB_EVALS surface (WR-B4-1,
// `/lab/v1/evaluations`). Records are the backends' own JSON (snake_case, verbatim) so nothing is
// re-derived here. With LAB_API_URL (else LAB_EVALS_API_URL) set the port is the HTTP adapter (server.ts); unset it is
// "unavailable" (fails closed), or, only outside production and only when asked for, the labelled
// preview fake.
import type { Actor } from "../../auth/access.ts";
import { FakeEvaluation } from "./fake.ts";
import { labEvaluation } from "./server.ts";

/** The one role table and actor (lib/auth/access.ts, contracts/v2 ROLE_CAPABILITIES): every role reads, developer and administrator run evaluations. */
export { holds, type Actor, type Capability } from "../../auth/access.ts";
/** F3 `Amount`: an exact 8-decimal string tagged with its unit; units are never mixed or converted. */
export type Amount = { unit: "CREDIT" | "PROVIDER_USD"; value: string };

/** D7's `lab_run_json`, verbatim: case and attempt counts by state, costs per unit. */
export type Run = {
  run_id: string;
  run_ref: string;
  dataset_ref: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  cases: Record<string, number>;
  attempts: Record<string, number>;
  costs: Record<string, string>;
};

/** B2's protocol: every threshold predeclared at launch, no platform default. */
export type Protocol = {
  confidence: number;
  margin: number;
  min_cases: number;
  metric_source: "deterministic_metric" | "teacher_judgment";
  required_slices: Record<string, { margin: number; min_cases: number }>;
  multifactor_tag?: string | null;
};
export type Estimate = {
  n: number;
  clusters: number;
  diff: number | null;
  low: number | null;
  high: number | null;
  verdict: "non_inferior" | "inferior" | "uncertain" | "insufficient";
  improved: boolean;
};
export type Observed = {
  mean: number;
  missing: number;
  errors: number;
  not_comparable: number;
  costs: Record<string, string>;
  latency_ms: { n: number; p50?: number; p90?: number; p99?: number; max?: number };
};
/** B2's `infrx.eval_report.1`, verbatim (the export is this object). */
export type Report = {
  schema: "infrx.eval_report.1";
  baseline_run: string;
  candidate_run: string;
  universe_digest: string;
  protocol: Protocol;
  protocol_digest: string;
  pairing: { kind: "rerun" | "single_factor" | "multifactor"; factors: string[]; tag: string | null };
  observed: { universe: number; baseline: Observed; candidate: Observed; cost_delta: Record<string, string>; paired: number };
  estimates: { basis: Protocol["metric_source"]; overall: Estimate; slices: Record<string, Estimate> };
  decision: { outcome: "accept" | "reject" | "inconclusive"; reasons: string[] };
  report_digest: string;
};
/** A baseline and a candidate run over one frozen suite; the report exists once B2 has compared them. */
export type Experiment = { experiment_id: string; created_at: string; protocol: Protocol; baseline: Run; candidate: Run; report: Report | null };
/** One serving factor differs; `experiment_id` is minted by the form, so a resubmit is the same launch. */
export type Launch = {
  experiment_id: string;
  dataset_ref: string;
  harness_ref: string;
  evaluator_ref: string;
  baseline_serving_ref: string;
  candidate_serving_ref: string;
  seed: number;
  max_cases: number;
  run_limit: Amount;
  protocol: Protocol;
};
export type Option = { ref: string; label: string };
/** H1 revisions: the harness is picked by its immutable ref, never edited here. */
export type Harness = { ref: string; harness_id: string; version: number; adapter: "text" | "finite_video" | "structured" };
export type Catalog = { datasets: Option[]; harnesses: Harness[]; servings: Option[]; evaluators: Option[] };

/** B3: the receipt's state (D7) and the subscription's one decision for it (the ledger). */
export type Decision = {
  checkpoint_id: string;
  step: number;
  receipt: "received" | "validated" | "rejected" | "evaluated";
  state: "queued" | "skipped" | null;
  reason: string | null;
  run_id: string | null;
};
export type SubscriptionRequest = {
  subscription_id: string;
  external_run_ref: string;
  dataset_ref: string;
  harness_ref: string;
  evaluator_ref: string;
  seed: number;
  max_cases: number;
  run_limit: Amount;
  limit: Amount;
  max_active: number;
  policy: "latest_only" | "every";
};
export type Subscription = SubscriptionRequest & { decisions: Decision[] };

export const REFUSALS = ["denied", "not_found", "invalid", "conflict", "unavailable"] as const;
export type Refusal = (typeof REFUSALS)[number];
export type Result<T> = { ok: true; value: T } | { ok: false; reason: Refusal };

export interface EvaluationPort {
  catalog(actor: Actor): Promise<Result<Catalog>>;
  runs(actor: Actor): Promise<Result<Run[]>>;
  experiments(actor: Actor): Promise<Result<Experiment[]>>;
  subscriptions(actor: Actor): Promise<Result<Subscription[]>>;
  /** Queues both runs as backend jobs (B1 `freeze`); nothing runs in the request. */
  launch(actor: Actor, launch: Launch): Promise<Result<Experiment>>;
  cancel(actor: Actor, runId: string): Promise<Result<Run>>;
  subscribe(actor: Actor, request: SubscriptionRequest): Promise<Result<Subscription>>;
}

const down = async () => ({ ok: false, reason: "unavailable" }) as const;
const UNAVAILABLE: EvaluationPort = { catalog: down, runs: down, experiments: down, subscriptions: down, launch: down, cancel: down, subscribe: down };

let preview: FakeEvaluation | undefined;
export const isPreview = (env: Record<string, string | undefined> = process.env) =>
  env.LAB_EVALS_PREVIEW === "1" && env.NODE_ENV !== "production";

/** WR-B4-1: the HTTP adapter when configured; otherwise unavailable. */
export function evaluationPort(env: Record<string, string | undefined> = process.env): EvaluationPort {
  if (isPreview(env)) return (preview ??= new FakeEvaluation());
  return labEvaluation(env) ?? UNAVAILABLE;
}
