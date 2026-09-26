/**
 * Usage page view model (U1R): the individual's requests from `consumer_jobs`, newest first, with
 * status, model/revision, tokens and — per settlement state, in the job's own unit — what was
 * charged or is still held.
 *
 * - A hold is not a charge and unknown usage is not a zero: only a settled job shows a charged
 *   amount, and a settled job whose charge cannot be read says "Unavailable".
 * - A legacy USD job is labelled USD; nothing here adds CREDIT and USD.
 * - The filters are `consumer_jobs`' own (0024): the model (requested or revision), the key, and the
 *   window [now - range, now). The database applies them inside its keyset scan, so every page is
 *   one read and a window's rows are exactly those in it. A row names no key (0024 note 3).
 *
 * Pure `.ts` with relative imports (R48).
 */

import { amount, num } from "../../../lib/format.ts";
import type { Page, Result } from "../../../lib/contracts/types.ts";
import type { ConsumerJob, JobsRequest } from "../billing/credit-reads.ts";
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

export type JobFilters = PageCursor & { range: JobRange; model: string | null; keyId: string | null };

/** A model name longer than any catalog id is not a filter; the URL is untrusted. */
export const MAX_MODEL_CHARS = 200;
const KEY_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function last(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[value.length - 1] : value;
}

/**
 * Unrecognised values are dropped: a key that is not a uuid (the form's "all" included) or an empty
 * or oversized model is no filter, never a database error.
 */
export function parseJobFilters(params: SearchParams): JobFilters {
  const range = last(params.range);
  const model = last(params.model)?.trim() ?? "";
  const key = last(params.key) ?? "";
  return {
    range: range !== undefined && Object.hasOwn(JOB_RANGES, range) ? (range as JobRange) : DEFAULT_JOB_RANGE,
    model: model !== "" && model.length <= MAX_MODEL_CHARS ? model : null,
    keyId: KEY_ID.test(key) ? key : null,
    ...parsePageCursor(params),
  };
}

export function jobsHref(filters: JobFilters): string {
  const search = new URLSearchParams();
  if (filters.range !== DEFAULT_JOB_RANGE) search.set("range", filters.range);
  if (filters.keyId !== null) search.set("key", filters.keyId);
  if (filters.model !== null) search.set("model", filters.model);
  if (filters.cursor !== null) search.set("cursor", filters.cursor);
  for (const cursor of filters.trail) search.append("trail", cursor);
  return hrefWith("/usage", search.toString());
}

/** In the DTO's instant form (`consumer_jobs` takes it as timestamptz). */
function instantOf(at: Date): string {
  return at.toISOString().replace(/Z$/, "000Z");
}

/** One page of `consumer_jobs`, narrowed by the filters; the window is [now - range, now). */
export function jobsPageRequest(filters: JobFilters, now: Date): JobsRequest {
  return {
    limit: JOB_PAGE_SIZE,
    cursor: filters.cursor,
    model: filters.model,
    keyId: filters.keyId,
    from: windowStart(filters.range, now),
    to: filters.range === "all" ? null : instantOf(now),
  };
}

/** The window's inclusive start in the DTO's instant form, or null for all history. */
export function windowStart(range: JobRange, now: Date): string | null {
  if (range === "all") return null;
  return instantOf(new Date(now.getTime() - JOB_RANGES[range]));
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

export function jobsPageModel(input: { filters: JobFilters; jobs: Result<Page<ConsumerJob>> }): JobsPageModel {
  const { filters } = input;
  const firstHref = jobsHref(firstCursorState(filters));
  return {
    filters,
    here: jobsHref(filters),
    firstHref,
    rows: mapState(viewStateOf(input.jobs, (page) => page.items.length === 0), (page) => ({
      rows: page.items.map(jobRowView),
      page: pageNumberOf(filters),
      firstHref,
      previousHref: hasPreviousPage(filters) ? jobsHref(previousCursorState(filters)) : null,
      nextHref: page.next_cursor === null ? null : jobsHref(nextCursorState(filters, page.next_cursor)),
    })),
    emptyText:
      filters.model !== null || filters.keyId !== null
        ? "No requests match this model or key. Clear the filters to see every request."
        : filters.range === "all"
          ? "No requests yet. Requests you send with an API key appear here, newest first."
          : `No requests in the last ${filters.range}. Choose a longer window to see earlier requests.`,
  };
}
