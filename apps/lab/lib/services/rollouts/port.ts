// R4: the Lab's view of the releases surface (WR-R4-1, `/lab/v1/releases`): D9's release rows and
// decisions, R1's aggregates, R2's latest verdict, R3's variants and comparisons, and expand/rollback
// proposals through the D9 CAS (an operator decides them via R2). With LAB_RELEASES_API_URL set the port
// is the HTTP adapter (server.ts); unset it is "unavailable" (fails closed), or, only outside production
// and only when asked for, the labelled preview fake. Actor, refusals and capabilities are L4's (contracts/v2 roles).
import { holds, REFUSALS, type Actor, type Refusal, type Result } from "../control/port.ts";
import { FakeReleases } from "./fake.ts";
import { labReleases } from "./server.ts";

export { holds, REFUSALS, type Actor, type Refusal, type Result };

export type Amount = { amount: string; unit: "CREDIT" | "PROVIDER_USD" };
/** R2's frozen `Plan` (every value a workload input; D9 stores its digest at launch). */
export type Plan = {
  horizonS: number; minRequests: number; maxErrorRate: number; maxP99Ms: number; maxSkewBp: number;
  minQualityCoverage: number; maxLagS: number; budget: Amount;
};
export type Arm = { requests: number; errors: number; p99Ms: number | null };
/** R1's aggregates for one policy revision (R2's `Live`) and its assignment tally: counts, no request ids. */
export type Progress = {
  observedUntil: string; baseline: Arm; candidate: Arm; qualityCovered: number; spent: Amount; candidateHealthy: boolean;
  assignments: { servingRef: string; pinnedBy: "cohort" | "explicit"; requests: number }[];
};
/** R2's latest `evaluate` of the release. */
export type Verdict = { action: "rollback" | "hold" | "expand"; reasons: string[]; evidenceRefs: string[]; evaluatedAt: string };
/** D9's row for one lab.rollout_policy.1 revision, with its latest progress and verdict (null: none yet).
 *  `refused: "unit_refused"` (R255): D9's Live refused this release's units (R248, legacy USD), so its progress
 *  and verdict are null for that reason, not because nothing is assigned; the key is absent otherwise. */
export type Release = {
  policyRef: string; endpointId: string; version: number; baselineRef: string; mode: "off" | "shadow" | "canary";
  cohort: "account" | "session"; candidates: { servingRef: string; weightBp: number }[];
  state: "running" | "approved" | "rolled_back"; fence: number; planDigest: string; plan: Plan; startedAt: string;
  progress: Progress | null; verdict: Verdict | null; refused?: "unit_refused";
};
/** A D9 transition: lab.rollout_decision.1 plus R2's reasons. */
export type Decision = {
  policyRef: string; decision: "expand" | "hold" | "rollback"; reasons: string[]; evidenceRefs: string[]; decidedBy: string; decidedAt: string;
};
export type ProposalKind = "expand" | "rollback";
export type Proposal = {
  proposalId: string; kind: ProposalKind; policyRef: string; fence: number; state: "proposed" | "approved" | "rejected";
  proposedAt: string; decidedAt: string | null;
};
export type Records = { releases: Release[]; decisions: Decision[]; proposals: Proposal[] };

/** R3's `Identity`, the fields that scope a comparison. */
export type Identity = { engine: string; engineVersion: string; hardware: string; quantization: string; capabilities: string[] };
/** R3's lab.optimization_variant.1 with its infrx.variant_comparison.1 (null: not compared). The identities are
 *  absent or null until R3 persists them (R252, WR-LW7-3a); the page then shows the serving refs. */
export type Variant = {
  variantRef: string; baseServingRef: string; variantServingRef: string; changes: string[]; base?: Identity | null; variant?: Identity | null;
  comparison: {
    outcome: "equivalent" | "not_equivalent" | "inconclusive" | "rejected"; reasons: string[]; reportDigest: string;
    performance: { throughputRatio: number; p99MsDelta: number; memoryGibDelta: number; sources: string[] } | null;
    optimizationClaimed: boolean;
  } | null;
};

export interface ReleasesPort {
  releases(actor: Actor): Promise<Result<Records>>;
  variants(actor: Actor): Promise<Result<Variant[]>>;
  /** A proposal against the revision the page showed (`fence`); an operator decides it outside the Lab. */
  propose(actor: Actor, kind: ProposalKind, policyRef: string, fence: number): Promise<Result<Proposal>>;
}

const down = async () => ({ ok: false, reason: "unavailable" }) as const;
const UNAVAILABLE: ReleasesPort = { releases: down, variants: down, propose: down };

let preview: FakeReleases | undefined;
export const isPreview = (env: Record<string, string | undefined> = process.env) =>
  env.LAB_RELEASES_PREVIEW === "1" && env.NODE_ENV !== "production";

export function releasesPort(env: Record<string, string | undefined> = process.env): ReleasesPort {
  if (isPreview(env)) return (preview ??= new FakeReleases());
  return labReleases(env) ?? UNAVAILABLE;
}
