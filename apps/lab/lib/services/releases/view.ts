// UX-10 (L-11): release and optimization evidence, derived only from the records. R4's rows
// (rollouts/view.ts) keep their decisions - plan, traffic, spend, verdict, lineage, which proposals a
// role may make; this adds what L-11 asks of the page: a measurement that has not happened says so, the
// current serving proof comes only from the latest observation (an approval is never proof), a proposal
// names the revision the server loaded, a stale one asks for review again, and an optimization is
// scoped: an identity not recorded or other hardware is not a comparison. Publication requests (AP-06)
// sit beside their revision's readiness (AP-05), never merged into one verdict.
import type { components } from "@infrx/api-client/lab";
import type { ApiError, Result } from "../../api/index.ts";
import { holds, type Role } from "../../auth/access.ts";
import { fixedCopy } from "../common.ts";
import { REFUSALS, type ProposalKind, type Records, type Release, type Variant } from "../rollouts/port.ts";
import { REFUSAL_COPY, releaseRows, variantRows, type ReleaseRow, type VariantRow } from "../rollouts/view.ts";
import type { Publication } from "./port.ts";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info";
export type Status = { tone: Tone; text: string };

export const NO_MEASUREMENT = "No measurement yet";
export const STALE_FENCE = "If a newer policy revision exists, the evidence you reviewed has changed: review the release again before proposing.";

/** `?refused=` is anyone's to write: only a known reason's fixed copy is shown; a stale fence asks for review. */
export const refusalCopy = fixedCopy(REFUSALS, { ...REFUSAL_COPY, conflict: `${REFUSAL_COPY.conflict} ${STALE_FENCE}` });

/** A failed read: denied only when the service said so (403 / `denied`); anything else is unavailable, never empty. */
export const failure = (r: { ok: false; reason: string } | { ok: false; error: ApiError }): "denied" | "unavailable" =>
  ("reason" in r ? r.reason === "denied" : r.error.status === 403) ? "denied" : "unavailable";

export const roleNote = (role: Role): string | null =>
  holds(role, "propose_publication") ? null : "Proposing an expansion or a rollback needs an administrator.";

const LABEL: Record<ProposalKind, string> = { expand: "Propose expansion", rollback: "Propose rollback" };
const VERB: Record<ProposalKind, string> = { expand: "expanding", rollback: "rolling back" };

/** The candidate's serving evidence: the latest observation, never the release's state or an approval. */
function proof(r: Release): Status {
  const live = r.progress;
  if (live === null) {
    return { tone: "neutral", text: r.refused === "unit_refused" ? "Not available: progress settled in another unit" : `${NO_MEASUREMENT}: nothing shows the candidate serving` };
  }
  return live.candidateHealthy
    ? { tone: "success", text: `Candidate healthy in the observation through ${live.observedUntil}` }
    : { tone: "danger", text: `Candidate unhealthy in the observation through ${live.observedUntil}` };
}

export type ReleaseView = ReleaseRow & { observed: string; proof: Status; proposals: { kind: ProposalKind; label: string; summary: string }[] };

export function releaseViews(role: Role, records: Records): ReleaseView[] {
  const rows = releaseRows(role, records);
  return records.releases.map((r, i) => ({
    ...rows[i],
    traffic: r.progress === null && r.refused === undefined ? NO_MEASUREMENT : rows[i].traffic,
    observed: r.progress ? `through ${r.progress.observedUntil}` : NO_MEASUREMENT,
    proof: proof(r),
    proposals: rows[i].actions.map((kind) => ({
      kind, label: LABEL[kind],
      summary: `Propose ${VERB[kind]} ${r.policyRef} at policy revision ${rows[i].fence}, as shown here. An infrx operator decides; nothing changes until then.`,
    })),
  }));
}

type S = components["schemas"];
const notFound = (e: ApiError) => (e.kind === "error" || e.kind === "refusal") && e.status === 404;

/** AP-05's readiness of one revision; an unmounted hosting service (LAB_HOSTING off) is "can't be checked", never a row. */
export function readinessStatus(r: Result<S["ReadinessDoc"]> | undefined): Status {
  if (r === undefined || !r.ok) {
    return r !== undefined && notFound(r.error)
      ? { tone: "neutral", text: "No serving proof recorded for this revision" }
      : { tone: "neutral", text: "Serving proof can't be checked right now" };
  }
  return r.data.ready
    ? { tone: "success", text: `Serving proof current: identity, smoke and health checks passed (checked ${r.data.checked_at})` }
    : { tone: "warning", text: `No current serving proof: ${r.data.reasons.join(", ")}` };
}

const REQUEST: Record<S["Proposal"]["state"], Status> = {
  proposed: { tone: "neutral", text: "Awaiting an operator's decision" },
  approved: { tone: "info", text: "Approved by an operator" },
  rejected: { tone: "warning", text: "Rejected by an operator" },
};

export type PublicationRow = { id: string; title: string; revision: string; requested: string; decided: string; request: Status; proof: Status; caveat: string | null; listed: string | null };

export function publicationRows({ proposals, deployments, readiness }: Publication): PublicationRow[] {
  if (!proposals.ok) return [];
  const known = new Map((deployments.ok ? deployments.data.data : []).map((d) => [d.deployment_revision_id, d]));
  return proposals.data.data.map((p) => {
    const d = known.get(p.deployment_revision_id);
    const evidence = readinessStatus(readiness[p.deployment_revision_id]);
    return {
      id: p.proposal_id, revision: p.deployment_revision_id, requested: p.proposed_at, decided: p.decided_at ?? "Not yet",
      title: `${p.kind === "publish" ? "Publish" : "Roll back"} ${d ? `${d.model_id} ${d.revision_label}` : `deployment revision ${p.deployment_revision_id}`}`,
      request: REQUEST[p.state], proof: evidence,
      caveat: p.state === "approved" && evidence.tone !== "success" ? "An approval is not proof that this revision is serving now." : null,
      listed: d?.visibility === "public" ? "Listed publicly" : null,
    };
  });
}

export type VariantView = VariantRow & { serving: string; digest: string; comparable: string | null };

/** R4's variant rows, scoped: a comparison needs both identities recorded and the same hardware. */
export function variantViews(variants: Variant[]): VariantView[] {
  const rows = variantRows(variants);
  return variants.map((v, i) => {
    const gap = v.base === null || v.variant === null ? "Not comparable: an identity is not recorded"
      : v.base.hardware !== v.variant.hardware ? `Not comparable: measured on different hardware (${v.base.hardware}, ${v.variant.hardware})` : null;
    const row = rows[i];
    return {
      ...row,
      base: v.base ? row.base : "Not recorded",
      variant: v.variant ? row.variant : "Not recorded",
      performance: gap !== null && v.comparison?.performance ? gap : row.performance,
      claim: gap === null ? row.claim : "no optimization claimed",
      comparable: gap,
      serving: `${v.variantServingRef} is a separate serving version; ${v.baseServingRef} is unchanged; it is replaced only through a release.`,
      digest: v.comparison?.reportDigest ?? "No comparison report",
    };
  });
}
