// WR-R4-1: the releases port over infrx-api's `/lab/v1/releases` and `/lab/v1/optimizations`
// (LAB_RELEASES), as the signed-in user: the credential is the session's own access token (none: nothing
// is sent) and the provider is the actor's, sent as `provider_org_id`; the route re-derives both. The
// route speaks snake_case (D9/R1/R2/R3's records), the port camelCase: every key is renamed, no value is
// touched. Lists come as `{data}`; every refusal is the route's status mapped to the port's reason; an
// answer holding one record the pages cannot read is unavailable (fails closed). The transport and the
// rename are the shared ones (../http.ts).
import { bool, camel, labClient, list, nul, num, obj, oneOf, opt, str, type HttpOptions } from "../http.ts";
import type { ReleasesPort } from "./port.ts";

export type { HttpOptions };

/** The port's records (camelCase, after the rename) as the pages read them. */
const AMOUNT = obj({ amount: str, unit: oneOf("CREDIT", "PROVIDER_USD") });
const ARM = obj({ requests: num, errors: num, p99Ms: nul(num) });
const RELEASE = obj({
  policyRef: str, endpointId: str, version: num, baselineRef: str, mode: oneOf("off", "shadow", "canary"), cohort: oneOf("account", "session"),
  candidates: list(obj({ servingRef: str, weightBp: num })), state: oneOf("running", "approved", "rolled_back"), fence: num, planDigest: str,
  plan: obj({ horizonS: num, minRequests: num, maxErrorRate: num, maxP99Ms: num, maxSkewBp: num, minQualityCoverage: num, maxLagS: num, budget: AMOUNT }),
  startedAt: str,
  progress: nul(obj({
    observedUntil: str, baseline: ARM, candidate: ARM, qualityCovered: num, spent: AMOUNT, candidateHealthy: bool,
    assignments: list(obj({ servingRef: str, pinnedBy: oneOf("cohort", "explicit"), requests: num })),
  })),
  verdict: nul(obj({ action: oneOf("rollback", "hold", "expand"), reasons: list(str), evidenceRefs: list(str), evaluatedAt: str })),
  refused: opt(oneOf("unit_refused")), // R255: present only on a row whose Live was refused by unit
});
const DECISION = obj({ policyRef: str, decision: oneOf("expand", "hold", "rollback"), reasons: list(str), evidenceRefs: list(str), decidedBy: str, decidedAt: str });
const PROPOSAL = obj({
  proposalId: str, kind: oneOf("expand", "rollback"), policyRef: str, fence: num, state: oneOf("proposed", "approved", "rejected"), proposedAt: str, decidedAt: nul(str),
});
const IDENTITY = obj({ engine: str, engineVersion: str, hardware: str, quantization: str, capabilities: list(str) });
// R263: 0058's listing names both identities R3 stored with the variant; null only for a variant stored
// before R3 recorded them (the page says so). An answer that omits them is not 0058's listing: unavailable.
const VARIANT = obj({
  variantRef: str, baseServingRef: str, variantServingRef: str, changes: list(str), base: nul(IDENTITY), variant: nul(IDENTITY),
  comparison: nul(obj({
    outcome: oneOf("equivalent", "not_equivalent", "inconclusive", "rejected"), reasons: list(str), reportDigest: str, optimizationClaimed: bool,
    performance: nul(obj({ throughputRatio: num, p99MsDelta: num, memoryGibDelta: num, sources: list(str) })),
  })),
});

export function httpReleases(options: HttpOptions): ReleasesPort {
  const { get, post } = labClient(options, "/lab/v1", { rename: camel });
  return {
    releases: (actor) => get(actor, "releases", obj({ releases: list(RELEASE), decisions: list(DECISION), proposals: list(PROPOSAL) })),
    variants: (actor) => get(actor, "optimizations", list(VARIANT)),
    propose: (actor, kind, policyRef, fence) => post(actor, "releases/proposals", PROPOSAL, { kind, policy_ref: policyRef, fence }),
  };
}
