// B4: rows and copy derived only from the backends' records. Nothing here remembers what a button did,
// rounds a statistic, or adds amounts of different units.
import type { Role } from "../../auth/access.ts";
import { holds, REFUSALS, type Estimate, type Refusal, type Report, type Run, type Subscription } from "./port.ts";

/** One line per unit, the exact string the record carries; never a total across units. */
export function units(amounts: Record<string, string>): string[] {
  const lines = Object.keys(amounts).sort().map((unit) => `${amounts[unit]} ${unit}`);
  return lines.length > 0 ? lines : ["none"];
}

const STATE_COPY: Record<Run["state"], string> = {
  queued: "queued · waiting for a worker",
  running: "running",
  succeeded: "finished",
  failed: "failed",
  cancelled: "cancelled",
};

export type RunRow = { id: string; ref: string; state: string; done: string; failed: string; skipped: string; open: string; attempts: string; costs: string[]; cancel: boolean };

/** Every count is out of all the run's cases, so a failure or an unfinished case is never dropped. */
export function runRow(role: Role, run: Run): RunRow {
  const n = (state: string) => run.cases[state] ?? 0;
  const total = Object.values(run.cases).reduce((a, b) => a + b, 0);
  const of = (count: number, what: string) => `${count} / ${total} ${what}`;
  const attempts = Object.keys(run.attempts).sort().map((s) => `${s} ${run.attempts[s]}`);
  return {
    id: run.run_id, ref: run.run_ref, state: STATE_COPY[run.state],
    done: of(n("done"), "done"), failed: of(n("failed"), "failed"), skipped: of(n("skipped"), "skipped"),
    open: of(n("pending") + n("leased"), "not finished"),
    attempts: attempts.length > 0 ? attempts.join(" · ") : "none yet",
    costs: units(run.costs),
    cancel: (run.state === "queued" || run.state === "running") && holds(role, "run_evaluation"),
  };
}

const VERDICT: Record<Estimate["verdict"], string> = {
  non_inferior: "non-inferior",
  inferior: "inferior",
  uncertain: "uncertain",
  insufficient: "too few cases to decide",
};
const OUTCOME: Record<Report["decision"]["outcome"], string> = {
  accept: "Accept: every predeclared criterion is met",
  reject: "Reject: a predeclared criterion failed",
  inconclusive: "Inconclusive: the evidence does not decide this comparison",
};

export type EstimateRow = { name: string; paired: string; diff: string; interval: string; margin: string; verdict: string; improved: string };

function estimateRow(name: string, e: Estimate, rule: { margin: number; min_cases: number }): EstimateRow {
  return {
    name, paired: `${e.n} paired · ${e.clusters} clusters · needs ${rule.min_cases}`,
    diff: e.diff === null ? "—" : String(e.diff),
    interval: e.low === null ? "—" : `[${e.low}, ${e.high}]`,
    margin: `−${rule.margin}`, verdict: VERDICT[e.verdict], improved: e.improved ? "improved" : "",
  };
}

/** B2's report as it is: its decision, every required slice, the intervals and the observed facts. */
export function comparison(r: Report) {
  const p = r.protocol;
  const latency = (l: Report["observed"]["baseline"]["latency_ms"]) =>
    l.n === 0 ? "no latencies" : `p50 ${l.p50} · p90 ${l.p90} · p99 ${l.p99} · max ${l.max} ms over ${l.n}`;
  return {
    outcome: OUTCOME[r.decision.outcome],
    reasons: r.decision.reasons,
    pairing: `${r.pairing.kind.replace("_", " ")}${r.pairing.factors.length > 0 ? `: ${r.pairing.factors.join(", ")}` : ""}${r.pairing.tag ? ` (${r.pairing.tag})` : ""}`,
    basis: r.estimates.basis.replace("_", " "),
    confidence: `${Math.round(p.confidence * 1000) / 10}% intervals`,
    estimates: [
      estimateRow("overall", r.estimates.overall, p),
      ...Object.keys(r.estimates.slices).sort().map((s) => estimateRow(`slice ${s}`, r.estimates.slices[s], p.required_slices[s])),
    ],
    paired: `${r.observed.paired} / ${r.observed.universe} cases paired`,
    runs: (["baseline", "candidate"] as const).map((name) => {
      const o = r.observed[name];
      return {
        name, mean: String(o.mean), missing: `${o.missing} / ${r.observed.universe} missing`, errors: `${o.errors} errored`,
        notComparable: `${o.not_comparable} not comparable`, costs: units(o.costs), latency: latency(o.latency_ms),
      };
    }),
    costDelta: units(r.observed.cost_delta),
    identity: [
      ["Report", r.report_digest], ["Baseline run", r.baseline_run], ["Candidate run", r.candidate_run],
      ["Case universe", r.universe_digest], ["Protocol", r.protocol_digest],
    ] as [string, string][],
  };
}

function outcome(d: Subscription["decisions"][number]): string {
  if (d.state === "queued") return `evaluation queued · run ${d.run_id}`;
  if (d.state === "skipped") return `skipped · ${d.reason}`;
  return d.receipt === "rejected" ? "not evaluated: the checkpoint was rejected" : "not decided yet";
}

export function subscriptionRow(s: Subscription) {
  return {
    id: s.subscription_id, externalRun: s.external_run_ref,
    suite: [["Dataset", s.dataset_ref], ["Harness", s.harness_ref], ["Evaluator", s.evaluator_ref], ["Seed", String(s.seed)], ["Cases", String(s.max_cases)]] as [string, string][],
    budget: `${s.run_limit.value} ${s.run_limit.unit} per run · ${s.limit.value} ${s.limit.unit} in total · ${s.max_active} at once`,
    policy: s.policy === "latest_only" ? "latest only: a checkpoint older than one already received is skipped as superseded" : "every checkpoint, in any order",
    decisions: s.decisions.map((d) => ({ checkpoint: d.checkpoint_id, step: String(d.step), receipt: d.receipt, outcome: outcome(d) })),
  };
}

export const REFUSAL_COPY: Record<Refusal, string> = {
  denied: "Your role in this workspace does not run evaluations.",
  not_found: "That record is not in this workspace.",
  invalid: "Those values were not accepted. Check the references, the budget and the protocol.",
  conflict: "That record has moved on or was already made with other values; this page shows its current state.",
  unavailable: "Evaluation records could not be read. Nothing is shown until they can be; try again shortly.",
};

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown. */
export function refusalCopy(value: unknown): string | null {
  return (REFUSALS as readonly unknown[]).includes(value) ? REFUSAL_COPY[value as Refusal] : null;
}
