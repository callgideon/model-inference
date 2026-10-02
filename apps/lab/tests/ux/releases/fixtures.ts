// UX-10 fixtures: synthetic, provider-owned records. Rollout releases and variants are R4's
// (tests/r/fixtures.ts); publication requests, deployments and readiness are the generated Lab
// client's records (AP-06's /lab/v1/control/proposals, the deployments listing, AP-05's readiness).
import type { components } from "@infrx/api-client/lab";
import type { Result } from "@infrx/api-client/transport";

export * from "../../r/fixtures.ts";

type S = components["schemas"];
export const DEP = "33333333-3333-4333-8333-333333333333";
export const DEP2 = "44444444-4444-4444-8444-444444444444";
export const AT = "2026-10-02T09:00:00Z";

export const pub = (over: Partial<S["Proposal"]> = {}): S["Proposal"] => ({
  proposal_id: "pp1", kind: "publish", deployment_revision_id: DEP, state: "proposed", proposed_at: AT, decided_at: null, ...over,
});
export const dep = (over: Partial<S["Deployment"]> = {}): S["Deployment"] => ({
  deployment_revision_id: DEP, model_id: "acme/marlin-sop", serving_version_id: "sv-1", revision_label: "r2", runtime: "vllm",
  schema_version: "1", rate_card_version: null, environment: "dev", visibility: "private", state: "active", smoke: "none", created_at: AT, ...over,
});
export const ready = (over: Partial<S["ReadinessDoc"]> = {}): S["ReadinessDoc"] => ({
  deployment_revision_id: DEP, serving_version_id: "sv-1", state: "ready_private", ready: true, reasons: [], checked_at: AT, ...over,
});

export const ok = <T>(data: T): Result<T> => ({ ok: true, status: 200, data, requestId: "r", location: null });
/** The Lab unit without LAB_HOSTING: FastAPI's own 404 `{"detail": "Not Found"}`, which the transport reads as malformed. */
export const UNMOUNTED: Result<never> = { ok: false, requestId: "r", error: { kind: "unavailable", status: 404, reason: "malformed" } };
export const NOT_FOUND: Result<never> = {
  ok: false, requestId: "r",
  error: { kind: "error", status: 404, code: "not_found", message: "no such deployment", requestId: "r", retryable: false, fieldErrors: [], operationId: null, resourceId: null },
};
export const DENIED: Result<never> = { ok: false, requestId: "r", error: { kind: "refusal", status: 403, reason: "denied" } };
export const DOWN: Result<never> = { ok: false, requestId: "r", error: { kind: "unavailable", status: null, reason: "network" } };
