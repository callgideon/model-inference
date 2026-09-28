// R4 fixtures: provider-owned, synthetic records in the shape the releases port serves (WR-R4-1).
import type { Decision, Identity, Proposal, Records, Release, Variant, Verdict } from "../../lib/services/rollouts/port.ts";

export const A = "11111111-1111-4111-8111-111111111111";
export const B = "22222222-2222-4222-8222-222222222222";
const uuid = (n: string) => `${n.repeat(8)}-${n.repeat(4)}-4${n.repeat(3)}-8${n.repeat(3)}-${n.repeat(12)}`;
export const ref = (kind: string, n: string, provider = A) => `lab:${kind}:${provider}:${uuid(n)}@sha256:${n.repeat(64)}`;
export const POLICY = ref("policy", "3");
export const BASE = ref("serving", "4");
export const CAND = ref("serving", "5");
export const RUNS = [ref("run", "6"), ref("run", "7")];
export const T = "2026-09-27T12:00:00Z";

export const release = (over: Partial<Release> = {}): Release => ({
  policyRef: POLICY, endpointId: uuid("9"), version: 1, baselineRef: BASE, mode: "canary", cohort: "account",
  candidates: [{ servingRef: CAND, weightBp: 500 }], state: "running", fence: 3, planDigest: `sha256:${"d".repeat(64)}`,
  plan: {
    horizonS: 86_400, minRequests: 500, maxErrorRate: 0.02, maxP99Ms: 4000, maxSkewBp: 200, minQualityCoverage: 0.5, maxLagS: 300,
    budget: { amount: "50.00", unit: "PROVIDER_USD" },
  },
  startedAt: "2026-09-27T00:00:00Z",
  progress: {
    observedUntil: T, baseline: { requests: 2280, errors: 10, p99Ms: 3000 }, candidate: { requests: 120, errors: 2, p99Ms: 3100 },
    qualityCovered: 60, spent: { amount: "12.40", unit: "PROVIDER_USD" }, candidateHealthy: true,
    assignments: [{ servingRef: BASE, pinnedBy: "cohort", requests: 2280 }, { servingRef: CAND, pinnedBy: "explicit", requests: 3 }],
  },
  verdict: { action: "hold", reasons: ["min_requests", "report_inconclusive"], evidenceRefs: [], evaluatedAt: T },
  ...over,
});
export const EXPAND: Verdict = { action: "expand", reasons: [], evidenceRefs: RUNS, evaluatedAt: T };
export const records = (releases: Release[], decisions: Decision[] = [], proposals: Proposal[] = []): Records => ({ releases, decisions, proposals });
export const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  proposalId: "p1", kind: "rollback", policyRef: POLICY, fence: 3, state: "proposed", proposedAt: T, decidedAt: null, ...over,
});

export const ident = (over: Partial<Identity> = {}): Identity => ({
  engine: "vllm", engineVersion: "0.11.2", hardware: "B300", quantization: "bf16", capabilities: ["text"], ...over,
});
export const SOURCES = ["marlin2b/results/load-bf16.json@abc1234", "marlin2b/results/load-nvfp4.json@abc1234"];
export const variant = (over: Partial<Variant> = {}, comparison: Partial<NonNullable<Variant["comparison"]>> = {}): Variant => ({
  variantRef: ref("variant", "8"), baseServingRef: BASE, variantServingRef: CAND, changes: ["quantization:nvfp4"],
  base: ident(), variant: ident({ quantization: "nvfp4" }),
  comparison: {
    outcome: "equivalent", reasons: [], reportDigest: `sha256:${"e".repeat(64)}`,
    performance: { throughputRatio: 1.42, p99MsDelta: -120, memoryGibDelta: -12, sources: SOURCES }, optimizationClaimed: true, ...comparison,
  },
  ...over,
});
