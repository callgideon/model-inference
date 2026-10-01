// WR-B4-1: the evaluation port over infrx-api's `/lab/v1/evaluations` (LAB_EVALS), as the signed-in
// user: the credential is the session's own access token (none: nothing is sent) and the provider is the
// actor's, sent as `provider_org_id`; the route re-derives both. Records are the backends' JSON verbatim
// (snake_case), lists come as `{data}`, every refusal is the route's status mapped to the port's reason,
// and an answer holding one record the pages cannot read is unavailable (fails closed). The transport is
// the shared one (../http.ts), with no key rename.
import { bool, labClient, list, map, nul, num, obj, oneOf, opt, str, type HttpOptions } from "../http.ts";
import type { EvaluationPort } from "./port.ts";

export type { HttpOptions };

const RUN = obj({
  run_id: str, run_ref: str, dataset_ref: str, state: oneOf("queued", "running", "succeeded", "failed", "cancelled"),
  cases: map(num), attempts: map(num), costs: map(str),
});
const PROTOCOL = obj({
  confidence: num, margin: num, min_cases: num, metric_source: oneOf("deterministic_metric", "teacher_judgment"),
  required_slices: map(obj({ margin: num, min_cases: num })),
});
const ESTIMATE = obj({
  n: num, clusters: num, diff: nul(num), low: nul(num), high: nul(num),
  verdict: oneOf("non_inferior", "inferior", "uncertain", "insufficient"), improved: bool,
});
const OBSERVED = obj({
  mean: num, missing: num, errors: num, not_comparable: num, costs: map(str),
  latency_ms: obj({ n: num, p50: opt(num), p90: opt(num), p99: opt(num), max: opt(num) }),
});
const REPORT = obj({
  schema: oneOf("infrx.eval_report.1"), baseline_run: str, candidate_run: str, universe_digest: str, protocol: PROTOCOL, protocol_digest: str,
  pairing: obj({ kind: oneOf("rerun", "single_factor", "multifactor"), factors: list(str), tag: nul(str) }),
  observed: obj({ universe: num, baseline: OBSERVED, candidate: OBSERVED, cost_delta: map(str), paired: num }),
  estimates: obj({ basis: oneOf("deterministic_metric", "teacher_judgment"), overall: ESTIMATE, slices: map(ESTIMATE) }),
  decision: obj({ outcome: oneOf("accept", "reject", "inconclusive"), reasons: list(str) }),
  report_digest: str,
});
const EXPERIMENT = obj({ experiment_id: str, created_at: str, protocol: PROTOCOL, baseline: RUN, candidate: RUN, report: nul(REPORT) });
const OPTION = obj({ ref: str, label: str });
const CATALOG = obj({
  datasets: list(OPTION), servings: list(OPTION), evaluators: list(OPTION),
  harnesses: list(obj({ ref: str, harness_id: str, version: num, adapter: oneOf("text", "finite_video", "structured") })),
});
const AMOUNT = obj({ unit: oneOf("CREDIT", "PROVIDER_USD"), value: str });
const SUBSCRIPTION = obj({
  subscription_id: str, external_run_ref: str, dataset_ref: str, harness_ref: str, evaluator_ref: str, seed: num, max_cases: num,
  run_limit: AMOUNT, limit: AMOUNT, max_active: num, policy: oneOf("latest_only", "every"),
  decisions: list(obj({
    checkpoint_id: str, step: num, receipt: oneOf("received", "validated", "rejected", "evaluated"),
    state: nul(oneOf("queued", "skipped")), reason: nul(str), run_id: nul(str),
  })),
});

export function httpEvaluation(options: HttpOptions): EvaluationPort {
  const { get, post } = labClient(options, "/lab/v1/evaluations");
  return {
    catalog: (actor) => get(actor, "catalog", CATALOG),
    runs: (actor) => get(actor, "runs", list(RUN)),
    experiments: (actor) => get(actor, "experiments", list(EXPERIMENT)),
    subscriptions: (actor) => get(actor, "subscriptions", list(SUBSCRIPTION)),
    launch: (actor, launch) => post(actor, "experiments", EXPERIMENT, launch),
    cancel: (actor, runId) => post(actor, `runs/${encodeURIComponent(runId)}/cancel`, RUN),
    subscribe: (actor, request) => post(actor, "subscriptions", SUBSCRIPTION, request),
  };
}
