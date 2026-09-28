/**
 * V1 — the trace list view model: a `Result<Page<TraceListItem>>` plus the reader's filters in,
 * everything the markup renders out. Pure, so `tests/v` can pin it under `node --test`.
 *
 * The one thing this module exists to get right: **content availability is five distinct states,
 * and only one of them is a failure.** A row whose content is `metadata_only` or `expired` is a
 * working request whose bytes were never stored or are past retention; a row that is `pending` is
 * a projection still catching up; only `lost` means capture failed, and then it says why (R13,
 * R42, 08 §9). Off-mode requests are not in this list at all — they have no trace row — so the
 * absence of a request must never be drawn as a loss.
 */

import {
  traceModeOf,
  type ApiKeySummary,
  type ErrorCode,
  type Page,
  type Result,
  type TraceContentAvailability,
  type TraceListItem,
  type TraceLossReason,
} from "../../../lib/contracts/types.ts";
import { traceHref, type FilterState } from "./query.ts";

export type ContentTone = "ok" | "muted" | "waiting" | "warn";

export type ContentPresentation = {
  state: TraceContentAvailability;
  label: string;
  tone: ContentTone;
  detail: string;
  /** True only where a capture actually failed. `metadata_only`, `expired` and `off` are not failures. */
  failed: boolean;
  /** The loss reason, on a failed row only; never borrowed by a state that did not fail. */
  reason: TraceLossReason | null;
  /** True where the state cannot legitimately appear in a list row (an `off` row: R13). */
  unexpected: boolean;
};

/** Why content is missing, in the reader's words. `none` on a lost row is itself a defect. */
const LOSS_DETAIL: Readonly<Record<TraceLossReason, string>> = {
  none: "No reason was recorded for the loss.",
  memory_budget: "The in-memory capture budget was full.",
  metadata_budget: "The metadata budget was full.",
  queue_full: "The capture queue was full.",
  disk_budget: "The spool disk budget was full.",
  disk_error: "The spool could not be written.",
  shutdown: "The capture was interrupted by a shutdown.",
  malformed: "The captured trace was rejected as malformed.",
  abandoned: "The capture was abandoned before it finished.",
};

/**
 * The availability mapping. Written as one total table rather than a chain of conditions, so a new
 * contract state is a compile error here instead of an unlabelled cell in the table.
 */
const CONTENT_PRESENTATION: Readonly<
  Record<
    TraceContentAvailability,
    Omit<ContentPresentation, "state" | "detail" | "reason"> & { detail?: string }
  >
> = {
  available: {
    label: "Full",
    tone: "ok",
    failed: false,
    unexpected: false,
    detail: "The request and response are stored.",
  },
  metadata_only: {
    label: "Metadata only",
    tone: "muted",
    failed: false,
    unexpected: false,
    detail: "This key captures metadata only, so no prompt or response was stored.",
  },
  pending: {
    label: "Pending",
    tone: "waiting",
    failed: false,
    unexpected: false,
    detail: "The trace has been accepted; its content has not been projected yet.",
  },
  lost: { label: "Lost", tone: "warn", failed: true, unexpected: false },
  expired: {
    label: "Expired",
    tone: "muted",
    failed: false,
    unexpected: false,
    detail: "The content is past the retention window this organization chose.",
  },
  off: {
    label: "Not captured",
    tone: "muted",
    failed: false,
    unexpected: true,
    detail: "Tracing was off for this request, so no content exists. Off-mode requests have no trace row.",
  },
};

export function presentContent(
  row: Pick<TraceListItem, "content" | "loss_reason">,
): ContentPresentation {
  const base = CONTENT_PRESENTATION[row.content];
  return {
    state: row.content,
    label: base.label,
    tone: base.tone,
    failed: base.failed,
    unexpected: base.unexpected,
    reason: base.failed ? row.loss_reason : null,
    detail: base.detail ?? LOSS_DETAIL[row.loss_reason],
  };
}

export type StatusTone = "ok" | "muted" | "warn" | "error";

/** 2xx reads as success, 4xx as the caller's problem, 5xx as ours; 0/unset as "no status yet". */
export function statusTone(httpStatus: number): StatusTone {
  if (httpStatus >= 500) return "error";
  if (httpStatus >= 400) return "warn";
  if (httpStatus >= 200) return "ok";
  return "muted";
}

export type TraceRowView = {
  requestId: string;
  href: string;
  createdAt: string;
  /** The key's name where this organization has one, else a short form of the opaque id. */
  keyLabel: string;
  keyRevoked: boolean;
  model: string;
  jobState: TraceListItem["job_state"];
  /** True while the request has not reached a terminal state: figures below are not final. */
  inFlight: boolean;
  httpStatus: number;
  statusTone: StatusTone;
  mode: TraceListItem["trace_mode"];
  content: ContentPresentation;
  promptTokens: number | null;
  completionTokens: number | null;
  ttftMs: number | null;
  wallMs: number;
  cost: TraceListItem["cost"];
  feedbackCount: number;
  scoreCount: number;
};

/** What the page tells the reader about this page of rows, beyond the rows themselves. */
export type TraceListIndicators = {
  rows: number;
  /** Projection lag: accepted, not yet projected. */
  pending: number;
  /** Capture loss, and why. */
  lost: number;
  lostReasons: { reason: TraceLossReason; count: number }[];
  metadataOnly: number;
  /** Rows still running: their tokens, cost and latency are not final. */
  inFlight: number;
  /**
   * Rows whose availability cannot legitimately appear in a list (`off`). Zero against a correct
   * service; surfaced rather than swallowed, because the alternative is drawing a data defect as a
   * capture failure.
   */
  unexpected: number;
};

export type EmptyReason = "filtered" | "tracing_off" | "no_traces" | "keys_unknown";

export type TraceListAction = { label: string; href: string };

export type TraceListView =
  | {
      kind: "rows";
      rows: TraceRowView[];
      indicators: TraceListIndicators;
      /** The next page, or null at the end of the walk. */
      nextHref: string | null;
      /** Back to the start of the walk, or null when already there. */
      firstHref: string | null;
    }
  | { kind: "empty"; reason: EmptyReason; message: string; action: TraceListAction | null }
  | { kind: "error"; code: ErrorCode; message: string; action: TraceListAction | null };

/**
 * Safe copy per error code. The service's own message is a fixed safe string, but the reader needs
 * to know what to do, which is a property of the code and of this page — and a retry is only
 * offered where retrying can work.
 */
const ERROR_COPY: Partial<Record<ErrorCode, { message: string; retry: boolean }>> = {
  invalid_cursor: {
    message: "That page link no longer matches the current filters. Start from the first page.",
    retry: false,
  },
  invalid_request: {
    message: "The filters in this link are not ones the trace list accepts. Clear them and try again.",
    retry: false,
  },
  forbidden: { message: "This account cannot read traces for this organization.", retry: false },
  not_found: { message: "No trace list exists for this organization.", retry: false },
  org_suspended: {
    message: "This organization is suspended. Traces stay readable — reload to try again.",
    retry: true,
  },
  rate_limited: { message: "Too many requests just now. Try again in a moment.", retry: true },
  dependency_unavailable: {
    message: "The trace store is not answering right now. Nothing is lost; try again.",
    retry: true,
  },
  deadline_exceeded: { message: "The query took too long. Narrow the window and try again.", retry: true },
  internal_error: { message: "Something went wrong reading traces. Try again.", retry: true },
};

const FALLBACK_ERROR = "Traces could not be loaded right now.";

function shortId(id: string): string {
  return `${id.slice(0, 8)}…`;
}

function countBy(rows: TraceRowView[], match: (row: TraceRowView) => boolean): number {
  return rows.filter(match).length;
}

const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled", "expired"]);

function rowView(item: TraceListItem, keys: Map<string, ApiKeySummary>): TraceRowView {
  const key = keys.get(item.key_id);
  return {
    requestId: item.request_id,
    href: `/traces/${item.request_id}`,
    createdAt: item.created_at,
    keyLabel: key?.name ?? shortId(item.key_id),
    keyRevoked: key !== undefined && key.revoked_at !== null,
    model: item.model,
    jobState: item.job_state,
    inFlight: !TERMINAL_STATES.has(item.job_state),
    httpStatus: item.http_status,
    statusTone: statusTone(item.http_status),
    mode: item.trace_mode,
    content: presentContent(item),
    promptTokens: item.prompt_tokens,
    completionTokens: item.completion_tokens,
    ttftMs: item.ttft_ms,
    wallMs: item.wall_ms,
    cost: item.cost,
    feedbackCount: item.feedback_count,
    scoreCount: item.score_count,
  };
}

function indicatorsOf(rows: TraceRowView[]): TraceListIndicators {
  const reasons = new Map<TraceLossReason, number>();
  for (const row of rows) {
    const reason = row.content.reason;
    if (reason !== null) reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
  }
  return {
    rows: rows.length,
    pending: countBy(rows, (row) => row.content.state === "pending"),
    lost: countBy(rows, (row) => row.content.failed),
    lostReasons: [...reasons.entries()]
      .map(([reason, count]) => ({ reason, count }))
      .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason)),
    metadataOnly: countBy(rows, (row) => row.content.state === "metadata_only"),
    inFlight: countBy(rows, (row) => row.inFlight),
    unexpected: countBy(rows, (row) => row.content.unexpected),
  };
}

/**
 * Build the view. `keys` comes from `keys.list` — the list row carries only an opaque `key_id`
 * (exactly 16 fields, no key name), so the name is joined here or the id is shown short.
 *
 * ponytail: the indicators count this page, not the organization's window. A window-wide
 * "3 traces lost in the last 24h" figure needs an aggregate the console contract does not have
 * (no `traceSummary`); recorded as a contract-revision request rather than guessed from 25 rows.
 */
export function buildTraceListView(
  result: Result<Page<TraceListItem>>,
  context: {
    filters: FilterState;
    keys: readonly ApiKeySummary[];
    narrowed?: boolean;
    /** True when `keys.list` failed: the names are missing and so is the "is anything captured?" fact. */
    keysUnavailable?: boolean;
  },
): TraceListView {
  // The way out of a bad link: no filters, no cursor and no pinned window. `cursor: null` has to
  // *clear* the cursor even though no filter changed, or this is a link to the error the reader is
  // already looking at.
  const clearHref = traceHref(context.filters, {
    range: context.filters.range,
    key: null,
    model: null,
    state: null,
    content: null,
    mode: null,
    feedback: "any",
    cursor: null,
    pinned: false,
  });

  if (!result.ok) {
    const copy = ERROR_COPY[result.error.code];
    return {
      kind: "error",
      code: result.error.code,
      message: copy?.message ?? FALLBACK_ERROR,
      action:
        copy === undefined || copy.retry
          ? { label: "Try again", href: traceHref(context.filters) }
          : { label: "Start from the first page", href: clearHref },
    };
  }

  const keys = new Map(context.keys.map((key) => [key.id, key]));
  const rows = result.value.items.map((item) => rowView(item, keys));

  if (rows.length === 0) {
    const narrowed = context.narrowed ?? false;
    if (narrowed) {
      return {
        kind: "empty",
        reason: "filtered",
        message: "No traces match these filters.",
        action: { label: "Clear filters", href: clearHref },
      };
    }
    // Without the key list there is no basis for saying whether anything is captured, and saying
    // "tracing is off for every key" on a failed read would be an invention.
    if (context.keysUnavailable === true) {
      return {
        kind: "empty",
        reason: "keys_unknown",
        message:
          "No traces in this window. This organization's keys could not be read, so whether tracing is on for them is unknown.",
        action: { label: "Try again", href: traceHref(context.filters) },
      };
    }
    // No filters and no rows: either nothing has run, or nothing is being captured. The two read
    // very differently to someone who has just made requests, so they are separate states.
    // r2: `ApiKeySummary.trace_mode` is nullable and **null is off** — a key that predates the
    // column recorded no choice, and capture is consent, so no choice can only mean no capture.
    // `key.trace_mode !== "off"` was true for such a key, so this page told an organization whose
    // keys capture nothing that it was capturing and that its empty list meant "nothing has run".
    // `traceModeOf` is the one reading of the column; the comparison never repeats it.
    const capturing = context.keys.some((key) => traceModeOf(key) !== "off" && key.revoked_at === null);
    return capturing
      ? {
          kind: "empty",
          reason: "no_traces",
          message: "No traces in this window yet.",
          action: null,
        }
      : {
          kind: "empty",
          reason: "tracing_off",
          message:
            "Tracing is off for every active key, so no traces are captured. Requests still appear under Usage.",
          action: { label: "Set a key's trace mode", href: "/api-keys" },
        };
  }

  return {
    kind: "rows",
    rows,
    indicators: indicatorsOf(rows),
    nextHref:
      result.value.next_cursor === null
        ? null
        : traceHref(context.filters, { cursor: result.value.next_cursor }),
    // Only the browser's back button walks the cursor backwards (there is no reverse cursor in v1),
    // so a reader deep in a walk gets one link that always works.
    firstHref:
      context.filters.cursor === null
        ? null
        : traceHref(context.filters, { cursor: null, pinned: false }),
  };
}
