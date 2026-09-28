// WR-B4-1: the evaluation port over infrx-api's `/lab/v1/evaluations` (LAB_EVALS), as the signed-in
// user: the credential is the session's own access token (none: nothing is sent) and the provider is the
// actor's, sent as `provider_org_id`; the route re-derives both. Records are the backends' JSON verbatim
// (snake_case), lists come as `{data}`, every refusal is the route's status mapped to the port's reason,
// and an answer holding one record the pages cannot read is unavailable (fails closed).
import type { Actor, EvaluationPort, Refusal, Result } from "./port.ts";
import { bool, list, map, nul, num, obj, oneOf, opt, str, type Check } from "./shape.ts";

const REASONS: Record<number, Refusal> = { 401: "denied", 403: "denied", 404: "not_found", 409: "conflict", 422: "invalid" };
export type HttpOptions = { baseUrl: string; token: () => Promise<string | null>; fetch?: typeof fetch };

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

export function httpEvaluation({ baseUrl, token, fetch: send = fetch }: HttpOptions): EvaluationPort {
  const root = `${baseUrl.replace(/\/+$/, "")}/lab/v1/evaluations`;
  async function call<T>(actor: Actor, method: "GET" | "POST", path: string, readable: Check, body?: unknown): Promise<Result<T>> {
    const bearer = await token().catch(() => null);
    if (!bearer) return { ok: false, reason: "unavailable" }; // no session: nothing is sent
    const url = `${root}/${path}?provider_org_id=${encodeURIComponent(actor.providerId)}`;
    const headers: Record<string, string> = { authorization: `Bearer ${bearer}` };
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      const response = await send(url, {
        method, headers, cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };
      const payload = await response.json();
      const value = method === "GET" ? payload.data : payload; // a read is {data}
      return readable(value) ? { ok: true, value: value as T } : { ok: false, reason: "unavailable" }; // an unreadable record
    } catch {
      return { ok: false, reason: "unavailable" }; // transport or an unparseable answer
    }
  }
  return {
    catalog: (actor) => call(actor, "GET", "catalog", CATALOG),
    runs: (actor) => call(actor, "GET", "runs", list(RUN)),
    experiments: (actor) => call(actor, "GET", "experiments", list(EXPERIMENT)),
    subscriptions: (actor) => call(actor, "GET", "subscriptions", list(SUBSCRIPTION)),
    launch: (actor, launch) => call(actor, "POST", "experiments", EXPERIMENT, launch),
    cancel: (actor, runId) => call(actor, "POST", `runs/${encodeURIComponent(runId)}/cancel`, RUN),
    subscribe: (actor, request) => call(actor, "POST", "subscriptions", SUBSCRIPTION, request),
  };
}
