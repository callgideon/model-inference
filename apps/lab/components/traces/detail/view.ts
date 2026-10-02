// One request: rows and copy derived only from the read record. Content access is the route's
// `access_state` (AP-07c); the body is shown only when that state says it is readable AND the record
// is a granted one carrying text - any other combination states why nothing is shown.
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import { accessLabel, elapsed, lossDetail, utc, type AccessLabel } from "../list/view-model.ts";
import type { AccessState, TraceDetail, TraceRefusal } from "./port.ts";

/** Another provider's request and one T2I has not projected yet are the same answer (TRACE-TENANT). */
export const TRACE_COPY: Record<TraceRefusal, string> = {
  not_found: "No request with this id is visible in this workspace. A request made in the last few minutes may not be projected yet; try again shortly.",
  denied: "Your role in this workspace sees aggregate health only, not individual requests.",
  unavailable: "Request records could not be read. Nothing is shown until they can be; try again shortly.",
};
export const TRACE_TITLE: Record<TraceRefusal, string> = {
  not_found: "Request not found",
  denied: "Individual requests aren't available to your role",
  unavailable: "We couldn't load this request",
};

/** Why there is no body, one sentence per state (03-lab.md L-05's access table). */
export const CONTENT_COPY: Record<Exclude<AccessState, "content" | "partial">, string> & { unread: string; unknown: string } = {
  metadata: "Content isn't shared with this workspace: its organization has no current grant sharing request content with you.",
  revoked: "Content access was revoked or has lapsed: the organization's grant to this workspace is no longer current, so nothing is shown.",
  expired: "This request's content is past its retention or was deleted, so it is not shown.",
  not_captured: "Content was not captured for this request.",
  unread: "The content could not be read just now. Nothing is shown until it can be; try again shortly.",
  unknown: "This request's content access could not be determined, so nothing is shown.",
};
export const PARTIAL_COPY = "Partial capture: the answer broke off, so the content below is incomplete.";

export type ContentRegion =
  | { kind: "body"; text: string; partial: boolean; provenance: [string, string][] }
  | { kind: "none"; copy: string };

/** The content region: a body only for content/partial on a granted record that carries text. */
export function contentRegion(d: TraceDetail): ContentRegion {
  const state = d.access_state;
  if (state === "content" || state === "partial") {
    if (d.access !== "content") return { kind: "none", copy: CONTENT_COPY.unknown };
    if (typeof d.content !== "string") return { kind: "none", copy: CONTENT_COPY.unread };
    const provenance: [string, string][] = [["Shared by organization", d.grantor_org_id], ["Grant", d.grant_ref], ["Size", `${d.content_bytes} bytes`]];
    return { kind: "body", text: d.content, partial: state === "partial", provenance };
  }
  if (state === "not_captured") {
    const why = lossDetail(d.loss_reason);
    return { kind: "none", copy: why === null ? `${CONTENT_COPY.not_captured} Capture was ${d.mode} for its key.` : `${CONTENT_COPY.not_captured} The capture was lost: ${why}.` };
  }
  return { kind: "none", copy: Object.hasOwn(CONTENT_COPY, state) ? CONTENT_COPY[state] : CONTENT_COPY.unknown };
}

export const access = (d: TraceDetail): AccessLabel => accessLabel(d.access_state);

/** Named fields only: anything else a record carries (an identity, content) is never rendered. */
export function metadataRows(d: TraceDetail): [string, string][] {
  return [
    ["Started", utc(d.started_at)],
    ["Completed", d.completed_at === null ? "Unfinished" : utc(d.completed_at)],
    ["Elapsed", elapsed(d.elapsed_ms)],
    ["Model revision", d.model_revision],
    ["Capture", d.mode],
    ["Loss", lossDetail(d.loss_reason) ?? "—"],
    ["Rate card", d.rate_card_version ?? "unpriced"],
    ["Price version", d.price_version],
    ["Policy", d.policy_version ?? "—"],
    ["Request schema", String(d.request_schema_version)],
  ];
}

export const FEEDBACK_COPY = {
  note: "Feedback as its author stored it, for review. Customer feedback is never a calibration label.",
  empty: "No feedback on this request yet.",
  not_found: "No feedback on this request is shared with this workspace (no current feedback grant).",
  forbidden: "Your role in this workspace does not allow reviewing feedback.",
  unavailable: "Feedback could not be read. Nothing is shown until it can be; try again shortly.",
} as const;

export type FeedbackRow = { id: string; what: string; text: string | null; who: string; when: string };

function what(name: string, value: boolean | number | string): string {
  if (name === "thumb") return value === true ? "thumbs up" : "thumbs down";
  if (name === "rating") return `rating ${value}`;
  return `${name}: ${value}`; // correction | comment
}

/** C3F's durable record, read independently of the trace projection (FEEDBACK-ACK). */
export function feedbackView(result: ReviewResult): { rows: FeedbackRow[]; note: string; empty: string | null } {
  if (!result.ok) return { rows: [], note: FEEDBACK_COPY.note, empty: FEEDBACK_COPY[result.reason] };
  const rows = result.entries.map((e) => ({
    id: e.feedback_id, what: what(e.name, e.value), text: e.comment, who: `${e.author_role} · ${e.channel}`, when: e.created_at,
  }));
  return { rows, note: FEEDBACK_COPY.note, empty: rows.length === 0 ? FEEDBACK_COPY.empty : null };
}
