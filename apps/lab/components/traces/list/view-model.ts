/**
 * V1M — the request list's view model (moved from the App's trace list, V1): a provider trace page in,
 * everything the markup renders out. Pure, so `tests/v/list` pins it under `node --test`.
 *
 * Rows come only from the provider trace read's named fields, so an organization, key, size or content
 * cannot reach the list; content is labelled, never shown (the detail page states it; C2 reads are WR-V2-2). A
 * missing piece of content is a state, and only a lost capture is a failure, with its reason. Requests
 * made with capture off have no trace row, so their absence is explained rather than drawn as a loss.
 */
import type { Result, TraceDetail, TraceRefusal } from "../detail/port.ts";
import { contentState, TRACE_COPY, type ContentState } from "../detail/view.ts";
import type { TracePage } from "../../../lib/services/traces/port.ts";
import { listHref } from "./query.ts";

export const CONTENT_LABEL: Record<ContentState, string> = {
  available: "Shared",
  metadata_only: "Metadata only",
  not_captured: "Not captured",
  lost: "Lost",
  expired: "Expired",
};

/** The trace contract's loss reasons (infrx/contracts/records.py TraceLossReason). */
type TraceLossReason =
  | "none" | "memory_budget" | "metadata_budget" | "queue_full" | "disk_budget" | "disk_error" | "shutdown" | "malformed" | "abandoned";

/** Why content is missing, in the reader's words; a reason outside the vocabulary is shown as recorded. */
const LOSS_DETAIL: Readonly<Record<TraceLossReason, string>> = {
  none: "no reason was recorded",
  memory_budget: "the in-memory capture budget was full",
  metadata_budget: "the metadata budget was full",
  queue_full: "the capture queue was full",
  disk_budget: "the spool disk budget was full",
  disk_error: "the spool could not be written",
  shutdown: "the capture was interrupted by a shutdown",
  malformed: "the captured trace was rejected as malformed",
  abandoned: "the capture was abandoned before it finished",
};

export const LIST_COPY = {
  empty: "No requests on this workspace's deployments yet. Requests made with capture off leave no trace record, so they are never listed here.",
  gap: "Nothing on this page is still retained (deleted or past retention). Older requests may follow.",
  not_found: "This workspace's requests could not be listed for this account. Reload, or choose the workspace again.",
} as const;

export type RequestRow = { requestId: string; href: string; started: string; duration: string; model: string; content: string };
export type ListView =
  | { kind: "rows"; rows: RequestRow[]; note: string | null; nextHref: string | null; firstHref: string | null }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string; firstHref: string | null };

function row(d: TraceDetail): RequestRow {
  const state = contentState(d);
  const reason = LOSS_DETAIL[d.loss_reason as TraceLossReason] ?? d.loss_reason;
  return {
    requestId: d.request_id,
    href: `/requests/${encodeURIComponent(d.request_id)}`,
    started: d.started_at,
    duration: d.completed_at === null ? "in progress" : `${Date.parse(d.completed_at) - Date.parse(d.started_at)} ms`,
    model: d.model_revision,
    content: state === "lost" ? `${CONTENT_LABEL.lost}: ${reason}` : CONTENT_LABEL[state],
  };
}

/** `cursor` is the page being shown (null = the first), so a later page can offer the way back. */
export function buildListView(result: Result<TracePage, TraceRefusal>, cursor: string | null): ListView {
  const firstHref = cursor === null ? null : listHref(null);
  if (!result.ok) return { kind: "error", message: result.reason === "not_found" ? LIST_COPY.not_found : TRACE_COPY[result.reason], firstHref };
  const { items, next_cursor } = result.value;
  if (items.length === 0 && next_cursor === null && cursor === null) return { kind: "empty", message: LIST_COPY.empty };
  return {
    kind: "rows",
    rows: items.map(row),
    // T3 hides deleted and expired requests after the page is cut, so a page can be empty mid-walk.
    note: items.length === 0 ? LIST_COPY.gap : null,
    nextHref: next_cursor === null ? null : listHref(next_cursor),
    firstHref,
  };
}
