/**
 * Usage page view model (U1R): the individual's requests from `consumer_jobs`, newest first, with
 * status, model/revision, tokens and — per settlement state, in the job's own unit — what was
 * charged or is still held.
 *
 * - A hold is not a charge and unknown usage is not a zero: only a settled job shows a charged
 *   amount, and a settled job whose charge cannot be read says "Unavailable".
 * - A legacy USD job is labelled USD; nothing here adds CREDIT and USD.
 * - The date window is cut on the ordered keyset stream (`created_at desc`): rows before its start
 *   are dropped and the walk ends there, so it stays one indexed read per page and a window's rows
 *   are exactly those at or after its start. `consumer_jobs` takes no key/model filter (wiring
 *   request to D10 in the U1R evidence), so the page offers none.
 *
 * Pure `.ts` with relative imports (R48).
 */

import { amount, num } from "../../../lib/format.ts";
import type { Page, Result } from "../../../lib/contracts/types.ts";
import type { ConsumerJob } from "../billing/credit-reads.ts";
import { requestDetailHref } from "../billing/credit-view-model.ts";
import {
  PAGE_SIZE,
  RANGES,
  firstCursorState,
  hasPreviousPage,
  hrefWith,
  instantLabel,
  mapState,
  nextCursorState,
  pageNumberOf,
  parsePageCursor,
  previousCursorState,
  viewStateOf,
  type PageCursor,
  type SearchParams,
  type ViewState,
} from "./view-model.ts";

// ---------------------------------------------------------------------------
// URL state
// ---------------------------------------------------------------------------

export const JOB_RANGES = { "24h": RANGES["24h"], "7d": RANGES["7d"], "30d": RANGES["30d"] } as const;
export type JobRange = keyof typeof JOB_RANGES | "all";
export const DEFAULT_JOB_RANGE: JobRange = "all";
export const JOB_PAGE_SIZE = PAGE_SIZE;

export type JobFilters = PageCursor & { range: JobRange };

export function parseJobFilters(params: SearchParams): JobFilters {
  const raw = params.range;
  const range = Array.isArray(raw) ? raw[raw.length - 1] : raw;
  return {
    range: range !== undefined && Object.hasOwn(JOB_RANGES, range) ? (range as JobRange) : DEFAULT_JOB_RANGE,
    ...parsePageCursor(params),
  };
}

export function jobsHref(filters: JobFilters): string {
  const search = new URLSearchParams();
  if (filters.range !== DEFAULT_JOB_RANGE) search.set("range", filters.range);
  if (filters.cursor !== null) search.set("cursor", filters.cursor);
  for (const cursor of filters.trail) search.append("trail", cursor);
  return hrefWith("/usage", search.toString());
}

/** A window change starts the walk again: the cursor was a position in the old window. */
export function withJobRange(filters: JobFilters, range: JobRange): JobFilters {
  return { range, cursor: null, trail: [] };
}

export function jobsPageRequest(filters: JobFilters): { limit: number; cursor: string | null } {
  return { limit: JOB_PAGE_SIZE, cursor: filters.cursor };
}

/** The window's inclusive start in the DTO's instant form, or null for all history. */
export function windowStart(range: JobRange, now: Date): string | null {
  if (range === "all") return null;
  return new Date(now.getTime() - JOB_RANGES[range]).toISOString().replace(/Z$/, "000Z");
}

// ---------------------------------------------------------------------------
// Row display
// ---------------------------------------------------------------------------

export type Tone = "neutral" | "positive" | "warning" | "muted";

export type ChargeView = {
  label: string;
  /** The charged amount; only a settled job has one. */
  amount: string | null;
  /** The outstanding reservation, when one is still held. Never the same cell as `amount`. */
  held: string | null;
  detail: string;
  tone: Tone;
};

export function jobChargeView(job: ConsumerJob): ChargeView {
  const holding = job.hold !== null && (job.holdState === "held" || job.holdState === "unknown");
  const held = holding ? amount(job.hold as string, job.unit) : null;
  switch (job.settlementState) {
    case null:
      return {
        label: "Pending",
        amount: null,
        held,
        detail: "Not settled yet. The hold is a ceiling reserved while the request runs, not a charge.",
        tone: "neutral",
      };
    case "settled":
      return {
        label: "Charged",
        amount: job.charged === null ? "Unavailable" : amount(job.charged, job.unit),
        held: null,
        detail:
          job.charged === null
            ? "Settled, but the charge could not be read. Nothing is shown as a zero."
            : "Measured usage, priced at the rate the request was admitted with.",
        tone: job.charged === null ? "warning" : "positive",
      };
    case "held_unknown":
      return {
        label: "Awaiting reconciliation",
        amount: null,
        held,
        detail:
          "The engine did not report usage we can trust. The hold stays reserved until it is reconciled; it is not a charge.",
        tone: "warning",
      };
    case "released_free":
      return {
        label: "No charge",
        amount: null,
        held: null,
        detail: "The request ended before billable work, so its hold was released.",
        tone: "muted",
      };
    case "released_platform_absorbed":
      return {
        label: "No charge",
        amount: null,
        held: null,
        detail: "The failure was ours. The hold was released and you were not charged.",
        tone: "muted",
      };
    default:
      return {
        label: "Unknown",
        amount: null,
        held,
        detail: "We cannot explain this request's settlement. Nothing is shown as charged.",
        tone: "neutral",
      };
  }
}

const STATE_LABELS: Record<string, string> = {
  preparing: "Preparing",
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  expired: "Expired",
};

export function jobStatus(job: ConsumerJob): string {
  const state = Object.hasOwn(STATE_LABELS, job.state) ? STATE_LABELS[job.state] : "Unknown state";
  const cause = job.outcomeCause;
  return cause === null || cause === "completed" ? state : `${state} · ${cause.replaceAll("_", " ")}`;
}

export type JobTokens = { input: string; output: string; note: string | null };

export function jobTokens(job: ConsumerJob): JobTokens {
  if (job.usageCertainty !== "authoritative" || job.promptTokens === null || job.completionTokens === null) {
    return {
      input: "—",
      output: "—",
      note: "Usage was not reported for this request. Nothing is estimated into a charge.",
    };
  }
  return { input: num(job.promptTokens), output: num(job.completionTokens), note: null };
}

export type JobRowView = {
  requestId: string;
  detailHref: string;
  when: string;
  model: string;
  revision: string;
  mode: string;
  status: string;
  tokens: JobTokens;
  unit: "credits" | "legacy USD";
  charge: ChargeView;
};

export function jobRowView(job: ConsumerJob): JobRowView {
  return {
    requestId: job.requestId,
    detailHref: requestDetailHref(job.requestId),
    when: instantLabel(job.createdAt),
    model: job.requestedModel,
    revision: job.modelRevision,
    mode: job.executionMode,
    status: jobStatus(job),
    tokens: jobTokens(job),
    unit: job.unit === "CREDIT" ? "credits" : "legacy USD",
    charge: jobChargeView(job),
  };
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export type JobRows = {
  rows: JobRowView[];
  page: number;
  firstHref: string;
  previousHref: string | null;
  nextHref: string | null;
};

export type JobsPageModel = {
  filters: JobFilters;
  here: string;
  firstHref: string;
  rows: ViewState<JobRows>;
  emptyText: string;
};

export function jobsPageModel(input: {
  filters: JobFilters;
  jobs: Result<Page<ConsumerJob>>;
  now: Date;
}): JobsPageModel {
  const { filters } = input;
  const start = windowStart(filters.range, input.now);
  const firstHref = jobsHref(firstCursorState(filters));
  // Newest first, so the first row before the window's start ends the window.
  const inWindow = (page: Page<ConsumerJob>): Page<ConsumerJob> => {
    if (start === null) return page;
    const kept = page.items.filter((job) => job.createdAt >= start);
    return kept.length === page.items.length ? page : { items: kept, next_cursor: null };
  };
  const windowed: Result<Page<ConsumerJob>> = input.jobs.ok
    ? { ok: true, value: inWindow(input.jobs.value) }
    : input.jobs;
  return {
    filters,
    here: jobsHref(filters),
    firstHref,
    rows: mapState(viewStateOf(windowed, (page) => page.items.length === 0), (page) => ({
      rows: page.items.map(jobRowView),
      page: pageNumberOf(filters),
      firstHref,
      previousHref: hasPreviousPage(filters) ? jobsHref(previousCursorState(filters)) : null,
      nextHref: page.next_cursor === null ? null : jobsHref(nextCursorState(filters, page.next_cursor)),
    })),
    emptyText:
      filters.range === "all"
        ? "No requests yet. Requests you send with an API key appear here, newest first."
        : `No requests in the last ${filters.range}. Choose a longer window to see earlier requests.`,
  };
}
