// R4: rows and copy derived only from the release and variant records. Nothing here remembers what a
// button did: a rollback is whatever D9's state and decisions say, a proposal only "awaiting operator".
import type { Role } from "../../auth/access.ts";
import { holds, REFUSALS, type Amount, type ProposalKind, type Records, type Refusal, type Variant } from "./port.ts";

export type ReleaseRow = {
  id: string; fence: number; setup: string; baseline: string; candidates: string[]; plan: string;
  traffic: string; errors: string; p99: string; quality: string; spend: string; assignments: string[];
  status: string; blocked: string | null; pending: string | null; lineage: string[]; actions: ProposalKind[];
};

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`;
const money = (a: Amount) => `${a.amount} ${a.unit}`;

export function releaseRows(role: Role, { releases, decisions, proposals }: Records): ReleaseRow[] {
  return releases.map((r) => {
    const { plan: p, progress: live, verdict: v } = r;
    const cand = live?.candidate;
    const pending = proposals.find((x) => x.policyRef === r.policyRef && x.state === "proposed");
    const inconclusive = v?.reasons.includes("report_inconclusive") ?? false;
    const actions: ProposalKind[] = [];
    if (!pending && holds(role, "propose_publication")) {
      if (r.state === "running" && v?.action === "expand") actions.push("expand");
      if (r.state !== "rolled_back") actions.push("rollback");
    }
    return {
      id: r.policyRef, fence: r.fence, setup: `${r.mode} · ${r.cohort} cohort · policy v${r.version}`, baseline: r.baselineRef,
      candidates: r.candidates.map((c) => `${c.servingRef} · ${(c.weightBp / 100).toFixed(2)}%`),
      plan: `horizon ${p.horizonS} s · at least ${p.minRequests} candidate requests · errors ≤ ${pct(p.maxErrorRate)} · p99 ≤ ${p.maxP99Ms} ms · cohort skew ≤ ${p.maxSkewBp} bp · quality coverage ≥ ${pct(p.minQualityCoverage)} · metrics lag ≤ ${p.maxLagS} s · budget ${money(p.budget)}`,
      traffic: live ? `candidate ${live.candidate.requests} · baseline ${live.baseline.requests} requests` : r.refused === "unit_refused" ? "progress unavailable: settled in another unit" : "no traffic observed",
      errors: cand && cand.requests > 0 ? pct(cand.errors / cand.requests) : "—",
      p99: cand?.p99Ms == null ? "—" : `${cand.p99Ms} ms`,
      quality: cand && cand.requests > 0 ? `${live.qualityCovered} of ${cand.requests}` : "—",
      spend: !live ? "—" : live.spent.unit !== p.budget.unit
        ? `spend in ${live.spent.unit} against a ${p.budget.unit} budget: not converted`
        : `${live.spent.amount} of ${money(p.budget)}`,
      assignments: (live?.assignments ?? []).map((a) => `${a.servingRef}: ${a.requests} (${a.pinnedBy})`),
      status: r.state === "rolled_back" ? `rolled back · serving returns to ${r.baselineRef}`
        : r.state === "approved" ? "expansion approved by an operator"
        : v ? `running · ${v.action}: ${v.reasons.join(", ") || "every guardrail passes"}` : "running · not evaluated yet",
      blocked: r.state !== "running" || v?.action === "expand" ? null
        : inconclusive ? "Promotion blocked: the evaluation is inconclusive." : "Promotion blocked until every guardrail passes.",
      pending: pending ? `${pending.kind} proposed · awaiting operator approval` : null,
      lineage: decisions.filter((d) => d.policyRef === r.policyRef).map((d) =>
        `${d.decision} by ${d.decidedBy} at ${d.decidedAt}${d.reasons.length ? `: ${d.reasons.join(", ")}` : ""}${d.evidenceRefs.length ? ` · evidence ${d.evidenceRefs.join(", ")}` : ""}`),
      actions,
    };
  });
}

export type VariantRow = { id: string; base: string; variant: string; changes: string; outcome: string; performance: string; claim: string; eligible: string };

// R3's `Load.source`: an experiment branch's results at a commit. Anything else is a fixture.
const RESULTS = /^(?:models\/)?(?:deepseek41f|deepseek41fnvfp4|qwen3827b|kimik3|marlin2b)\/results\/\S+@[0-9a-f]{7,40}$/;
// R252: an identity R3 has not persisted yet (absent or null) shows the serving ref instead.
const scope = (i: Variant["base"], servingRef: string) =>
  i ? `${i.engine} ${i.engineVersion} on ${i.hardware} · ${i.quantization} · ${i.capabilities.join(", ")}` : servingRef;

export function variantRows(variants: Variant[]): VariantRow[] {
  return variants.map((v) => {
    const c = v.comparison;
    const perf = c?.performance ?? null;
    const measured = perf !== null && perf.sources.every((s) => RESULTS.test(s));
    const outcome = c ? `${c.outcome}${c.reasons.length ? `: ${c.reasons.join(", ")}` : ""}` : "not compared";
    return {
      id: v.variantRef, base: scope(v.base, v.baseServingRef), variant: scope(v.variant, v.variantServingRef), changes: v.changes.join(", "), outcome,
      performance: perf === null ? "not measured" : !measured ? "synthetic fixture, not a measurement"
        : `measured: throughput ×${perf.throughputRatio.toFixed(2)} · p99 ${perf.p99MsDelta} ms · memory ${perf.memoryGibDelta.toFixed(1)} GiB (${perf.sources.join(", ")})`,
      claim: c?.outcome === "equivalent" && c.optimizationClaimed && measured ? "optimization claimed for this scope only" : "no optimization claimed",
      eligible: c?.outcome === "equivalent" ? "eligible as a release candidate" : `not eligible: ${outcome}`,
    };
  });
}

export const REFUSAL_COPY: Record<Refusal, string> = {
  denied: "Your role in this workspace does not allow that.",
  not_found: "That release is not in this workspace.",
  invalid: "That request was not accepted. Reload the page and try again.",
  conflict: "That release has moved on or already has a pending request; this page shows its current state.",
  unavailable: "Release records could not be read. Nothing is shown until they can be; try again shortly.",
};

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown. */
export function refusalCopy(value: unknown): string | null {
  return (REFUSALS as readonly unknown[]).includes(value) ? REFUSAL_COPY[value as Refusal] : null;
}
