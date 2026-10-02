/**
 * The request list's view model: a provider trace page in, everything the markup renders out. Pure, so
 * it runs under `node --test`.
 *
 * Rows come only from the trace read's named fields, so an organization, key, size or content cannot
 * reach the list. Content access is the route's `access_state` (AP-07c), labelled, never re-derived;
 * the loss reason is its own column. A filter is the route's: a row outside it means the read is not
 * what the page says it is, so the page is unavailable rather than a list that looks filtered.
 */
import type { Tone } from "../../ui/badge.tsx";
import { NO_FILTER, type AccessState, type Result, type TraceDetail, type TraceFilter, type TraceRefusal } from "../detail/port.ts";
import type { TracePage } from "../../../lib/services/traces/port.ts";
import { listHref } from "./query.ts";

export type AccessLabel = { label: string; tone: Tone };
export const ACCESS_LABEL: Record<AccessState, AccessLabel> = {
  content: { label: "Shared", tone: "info" },
  partial: { label: "Partial capture", tone: "warning" },
  metadata: { label: "Metadata only", tone: "neutral" },
  revoked: { label: "Access revoked", tone: "neutral" },
  expired: { label: "Expired", tone: "neutral" },
  not_captured: { label: "Not captured", tone: "neutral" },
};
const UNKNOWN: AccessLabel = { label: "Unknown", tone: "warning" };
/** A state outside the route's ladder is unknown: never shared, never content. */
export const accessLabel = (state: string): AccessLabel => ACCESS_LABEL[state as AccessState] ?? UNKNOWN;

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
/** null when nothing was lost. */
export const lossDetail = (reason: string): string | null =>
  reason === "none" ? null : (LOSS_DETAIL[reason as TraceLossReason] ?? reason);

/** An instant as UTC to the second (the ISO value stays in `<time dateTime>`); unparseable is shown raw. */
export function utc(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : `${at.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}
export const elapsed = (ms: number | null): string => (ms === null ? "Unfinished" : `${ms} ms`);

export const LIST_COPY = {
  empty: "No requests returned for this workspace",
  filteredEmpty: "No requests returned for this filter",
  emptyWhy: "Requests made with capture off leave no trace record, so they are never listed here.",
  gap: "Nothing on this page is still retained (deleted or past retention). Older requests may follow.",
  unavailable: "We couldn't load requests",
  unavailableWhy: "The request records could not be read. Nothing is shown until they can be.",
  denied: "Individual requests aren't available to your role",
  deniedWhy: "Your role in this workspace sees aggregate health only, not individual requests.",
  not_found: "This workspace's requests could not be listed",
  notFoundWhy: "This account could not list this workspace's requests. Reload, or choose the workspace again.",
} as const;
const REFUSED: Record<TraceRefusal, { title: string; message: string }> = {
  unavailable: { title: LIST_COPY.unavailable, message: LIST_COPY.unavailableWhy },
  denied: { title: LIST_COPY.denied, message: LIST_COPY.deniedWhy },
  not_found: { title: LIST_COPY.not_found, message: LIST_COPY.notFoundWhy },
};

export type RequestRow = {
  requestId: string; href: string; startedAt: string; started: string; elapsed: string;
  model: string; modelHref: string; revision: string; mode: string; access: AccessLabel; loss: string | null;
};
export type ListView =
  | { kind: "rows"; rows: RequestRow[]; note: string | null; nextHref: string | null; firstHref: string | null }
  | { kind: "empty"; title: string; message: string; clearHref: string | null }
  | { kind: "error"; state: TraceRefusal; title: string; message: string; retryHref: string; firstHref: string | null };

function row(d: TraceDetail): RequestRow {
  return {
    requestId: d.request_id,
    href: `/requests/${encodeURIComponent(d.request_id)}`,
    startedAt: d.started_at,
    started: utc(d.started_at),
    elapsed: elapsed(d.elapsed_ms),
    model: d.model_id,
    modelHref: listHref(null, { ...NO_FILTER, model_id: d.model_id }),
    revision: d.model_revision,
    mode: d.mode,
    access: accessLabel(d.access_state),
    loss: lossDetail(d.loss_reason),
  };
}

const outside = (d: TraceDetail, f: TraceFilter) =>
  (f.model_id !== null && d.model_id !== f.model_id) || (f.serving_version_id !== null && d.serving_version_id !== f.serving_version_id);

/** `cursor` is the page being shown (null = the first), so a later page can offer the way back. */
export function buildListView(result: Result<TracePage, TraceRefusal>, cursor: string | null, filter: TraceFilter = NO_FILTER): ListView {
  const firstHref = cursor === null ? null : listHref(null, filter);
  const refused = (state: TraceRefusal): ListView => ({ kind: "error", state, ...REFUSED[state], retryHref: listHref(cursor, filter), firstHref });
  if (!result.ok) return refused(result.reason);
  const { items, next_cursor } = result.value;
  if (items.some((d) => outside(d, filter))) return refused("unavailable");
  const filtered = filter.model_id !== null || filter.serving_version_id !== null;
  if (items.length === 0 && next_cursor === null && cursor === null) {
    return { kind: "empty", title: filtered ? LIST_COPY.filteredEmpty : LIST_COPY.empty, message: LIST_COPY.emptyWhy, clearHref: filtered ? listHref(null) : null };
  }
  return {
    kind: "rows",
    rows: items.map(row),
    // T3 hides deleted and expired requests after the page is cut, so a page can be empty mid-walk.
    note: items.length === 0 ? LIST_COPY.gap : null,
    nextHref: next_cursor === null ? null : listHref(next_cursor, filter),
    firstHref,
  };
}
