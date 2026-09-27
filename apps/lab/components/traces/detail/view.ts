// V2: rows and copy for one request, derived only from the read records. Nothing here remembers
// what a button did, and every missing piece of content reads as a sentence, never a blank.
import type { ReviewResult } from "../../../lib/services/review/index.ts";
import type { Actor, ContentPort, TraceDetail, TraceRefusal } from "./port.ts";

/** Another provider's request and one T2I has not projected yet are the same answer (TRACE-TENANT). */
export const TRACE_COPY: Record<TraceRefusal, string> = {
  not_found: "No request with this id is visible in this workspace. A request made in the last few minutes may not be projected yet; try again shortly.",
  denied: "Your role in this workspace sees aggregate health only, not individual requests.",
  unavailable: "Request records could not be read. Nothing is shown until they can be; try again shortly.",
};

export type ContentState = "available" | "not_captured" | "lost" | "metadata_only" | "expired" | "revoked" | "unavailable";
export const CONTENT_COPY: Record<ContentState | "partial", string> = {
  available: "Content is not loaded until you ask for it.",
  partial: "The captured content is incomplete: it was truncated at capture.",
  not_captured: "Content was not captured for this request: capture was off or metadata-only for its key.",
  lost: "This request's capture was lost before it was stored; only its metadata exists.",
  metadata_only: "Metadata only: this request's organization has no current grant sharing its content with this workspace (never given, revoked or expired).",
  expired: "This request's content is past its retention or was deleted, so it is not shown.",
  revoked: "The grant that shared this request's content is no longer current, so it is not shown.",
  unavailable: "Content could not be read. Nothing is shown until it can be; try again shortly.",
};

/** Named fields only: anything else a record carries (an identity, content) is never rendered. */
export function metadataRows(d: TraceDetail): [string, string][] {
  const done = d.completed_at === null ? null : Date.parse(d.completed_at) - Date.parse(d.started_at);
  const rows: [string, string][] = [
    ["Request", d.request_id],
    ["Started", d.started_at],
    ["Completed", d.completed_at ?? "in progress"],
    ["Duration", done === null ? "—" : `${done} ms`],
    ["Model", d.model_id],
    ["Model revision", d.model_revision],
    ["Serving version", d.serving_version_id],
    ["Rate card", d.rate_card_version ?? "unpriced"],
    ["Policy", d.policy_version ?? "—"],
    ["Capture", d.mode],
    ["Loss", d.loss_reason === "none" ? "—" : d.loss_reason],
  ];
  if (d.access === "content") rows.push(["Organization", d.grantor_org_id], ["Content size", `${d.content_bytes} bytes`]);
  return rows;
}

/** What the records say about content before anyone asks for it. */
export function contentState(d: TraceDetail): ContentState {
  if (d.mode !== "full") return "not_captured";
  if (d.loss_reason !== "none") return "lost";
  if (d.access === "metadata") return "metadata_only";
  return d.content_available ? "available" : "expired";
}

export type ContentView = { state: ContentState; copy: string; text: string | null; expiresAt: string | null; offer: boolean };
const view = (state: ContentState, offer = false): ContentView => ({ state, copy: CONTENT_COPY[state], text: null, expiresAt: null, offer });

/** Read through C2 only when asked (`wanted`), only when the records allow it; a refusal is never text. */
export async function contentView(port: ContentPort, actor: Actor, d: TraceDetail, wanted: boolean): Promise<ContentView> {
  const state = contentState(d);
  if (state !== "available" || d.access !== "content") return view(state);
  if (!wanted) return view(state, true);
  const read = await port.read(actor, d.grant_ref, d.request_id);
  if (!read.ok) return view(read.reason === "expired" ? "expired" : read.reason === "unavailable" ? "unavailable" : "revoked");
  const copy = d.content_complete ? "" : CONTENT_COPY.partial;
  return { state, copy, text: read.value.text, expiresAt: read.value.expiresAt, offer: false };
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
