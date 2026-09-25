/**
 * Request detail view model (U4): one owned request from `consumer_jobs` — phase, status, exact
 * charge state in the job's own unit, a sanitized failure explanation, and whether its result can
 * be read — plus the rules the browser pieces follow (poll backoff and loop, the result fetch and
 * its answer, the expiry timer, the back/forward re-read), with timers, clock and events injected.
 *
 * Result access is the contract's read classification (`ReadOutcome`, F2C.b), applied to the
 * persisted fields `consumer_jobs` returns; "available vs expired" is the database's own
 * `result_available` (its clock, the persisted expiry), never this server's or the browser's.
 *
 * Pure `.ts` with relative imports (R48).
 */

import type { ReadOutcome } from "../../../../lib/contracts/v2/lifecycle.ts";
import type { Result } from "../../../../lib/contracts/types.ts";
import type { ConsumerJob } from "../../billing/credit-reads.ts";
import { jobChargeView, jobStatus, jobTokens, type ChargeView, type JobTokens } from "../credit-view-model.ts";
import { instantLabel, mapState, viewStateOf, type ViewState } from "../view-model.ts";

// ---------------------------------------------------------------------------
// Result access
// ---------------------------------------------------------------------------

const TERMINAL = ["succeeded", "failed", "cancelled", "expired"];

/** F2C.b `read_outcome` over the persisted fields: what the API's result endpoint would answer. */
export function resultAccessOf(job: ConsumerJob): ReadOutcome {
  if (!TERMINAL.includes(job.state)) return "pending";
  if (job.settlementState === "held_unknown") return "held_unknown";
  if (job.state !== "succeeded" || job.usageCertainty !== "authoritative") return "no_result";
  if (job.resultExpiresAt === null) return "unavailable";
  return job.resultAvailable ? "available" : "expired";
}

// ---------------------------------------------------------------------------
// Failure copy: what happened and what to do, with no internal names
// ---------------------------------------------------------------------------

export type Failure = { title: string; action: string };

const OURS = "Nothing you sent caused this. You can send the request again; it is a new request.";

const FAILURES: Record<string, Failure> = {
  client_cancelled: { title: "You cancelled this request.", action: "Nothing more will run for it. Send a new request if you still need an answer." },
  client_disconnected: {
    title: "Your client disconnected before the response finished.",
    action: "Keep the connection open until the response ends, or submit an async job and fetch its result later.",
  },
  sync_deadline: {
    title: "The request did not finish within the time allowed for a synchronous call.",
    action: "Long inputs are better submitted as an async job, whose result you fetch when it is ready.",
  },
  queue_wait_expired: {
    title: "The request waited too long for capacity and was not run.",
    action: "Try again later. If this keeps happening, send fewer requests at once.",
  },
  deadline_exceeded: {
    title: "The request did not finish before its deadline.",
    action: "Try a shorter input or a smaller output limit, or submit it as an async job.",
  },
  invalid_media: {
    title: "The media in this request could not be used.",
    action: "Check that each media URL is reachable and the file is a supported format within the model's published limits.",
  },
  preparation_failed: {
    title: "We could not prepare this request's input.",
    action: "Check the input against the model's published limits, then send it again.",
  },
};

function failureOf(job: ConsumerJob): Failure | null {
  const cause = job.outcomeCause;
  if (cause === null || cause === "completed") return null;
  if (Object.hasOwn(FAILURES, cause)) return FAILURES[cause];
  // engine_error, engine_incomplete, lost_after_publication, retries_exhausted, platform_error,
  // journal_write_failed and anything newer: a platform-side failure.
  return { title: "The request failed on our side.", action: OURS };
}

// ---------------------------------------------------------------------------
// The detail
// ---------------------------------------------------------------------------

export type Phase = "waiting" | "running" | "finished" | "unknown";

const PHASES: Record<string, Phase> = {
  preparing: "waiting",
  queued: "waiting",
  running: "running",
  succeeded: "finished",
  failed: "finished",
  cancelled: "finished",
  expired: "finished",
};

export const PHASE_LABELS: Record<Phase, string> = {
  waiting: "Waiting to run",
  running: "Running",
  finished: "Finished",
  unknown: "Unknown",
};

export type ResultSection = {
  access: ReadOutcome;
  /** The persisted expiry, for the browser to drop content at; null when there is none. */
  expiresAt: string | null;
  note: string;
};

export type RequestDetail = {
  requestId: string;
  created: string;
  model: string;
  revision: string;
  mode: string;
  phase: Phase;
  status: string;
  tokens: JobTokens;
  unit: "credits" | "legacy USD";
  charge: ChargeView;
  failure: Failure | null;
  result: ResultSection;
  /** Re-read while the request is unfinished. */
  poll: boolean;
};

function resultNote(access: ReadOutcome, expiresAt: string | null): string {
  const at = expiresAt === null ? "" : instantLabel(expiresAt);
  switch (access) {
    case "available":
      return `Kept until ${at}. After that the content is removed; this page keeps the request's details and charge.`;
    case "expired":
      return `The result expired at ${at} and its content was removed. The request's details and charge stay here.`;
    case "pending":
      return "The result appears here when the request finishes.";
    case "held_unknown":
      return "This request's usage was not reported, so its result is not served while it awaits reconciliation.";
    case "no_result":
      return "This request did not produce a result.";
    case "unavailable":
      return "This result was stored without an expiry date, so it cannot be shown.";
  }
}

export function requestDetail(job: ConsumerJob): RequestDetail {
  const access = resultAccessOf(job);
  const phase = Object.hasOwn(PHASES, job.state) ? PHASES[job.state] : "unknown";
  return {
    requestId: job.requestId,
    created: instantLabel(job.createdAt),
    model: job.requestedModel,
    revision: job.modelRevision,
    mode: job.executionMode,
    phase,
    status: jobStatus(job),
    tokens: jobTokens(job),
    unit: job.unit === "CREDIT" ? "credits" : "legacy USD",
    charge: jobChargeView(job),
    failure: failureOf(job),
    result: { access, expiresAt: job.resultExpiresAt, note: resultNote(access, job.resultExpiresAt) },
    poll: phase === "waiting" || phase === "running",
  };
}

export type RequestDetailModel =
  | Exclude<ViewState<RequestDetail>, { kind: "error" }>
  | (Extract<ViewState<RequestDetail>, { kind: "error" }> & { poll: boolean });

/** `empty` = not found: another tenant's request and a nonexistent one read the same. */
export function requestDetailModel(read: Result<ConsumerJob | null>): RequestDetailModel {
  const state = mapState(viewStateOf(read, (job) => job === null), (job) => requestDetail(job as ConsumerJob));
  // An outage is re-read with the same backoff; a refusal is not.
  return state.kind === "error" ? { ...state, poll: state.recovery === "retry" } : state;
}

/** Whether the page mounts the poller: an unfinished request, or a read that hit an outage. */
export function pollsFor(model: RequestDetailModel): boolean {
  return model.kind === "ready" ? model.value.poll : model.kind === "error" && model.poll;
}

// ---------------------------------------------------------------------------
// Polling: bounded exponential backoff
// ---------------------------------------------------------------------------

export const MAX_POLLS = 20;
const FIRST_POLL_MS = 2000;
const MAX_POLL_MS = 30_000;

/** The wait before poll `attempt` (0-based), or null once the poller should stop (≈ 8.5 min). */
export function pollDelayMs(attempt: number): number | null {
  if (!Number.isInteger(attempt) || attempt < 0 || attempt >= MAX_POLLS) return null;
  return Math.min(FIRST_POLL_MS * 2 ** attempt, MAX_POLL_MS);
}

/** Arms `run` after `ms` milliseconds; the answer cancels it. */
export type Schedule = (run: () => void, ms: number) => () => void;

export const browserTimer: Schedule = (run, ms) => {
  const id = setTimeout(run, ms);
  return () => clearTimeout(id);
};

/** Calls `refresh` on the backoff, then `onStop` after the last poll. The answer cancels (unmount). */
export function pollLoop(schedule: Schedule, refresh: () => void, onStop: () => void): () => void {
  let cancel = () => {};
  const arm = (attempt: number) => {
    const delay = pollDelayMs(attempt);
    if (delay === null) return onStop();
    cancel = schedule(() => {
      refresh();
      arm(attempt + 1);
    }, delay);
  };
  arm(0);
  return () => cancel();
}

// ---------------------------------------------------------------------------
// The browser's result fetch
// ---------------------------------------------------------------------------

export type ResultRead =
  | { state: "ready"; text: string }
  | { state: "pending" | "withheld" | "no_result" | "expired" | "unavailable" | "not_found" | "signed_out" };

/** What the result panel shows: a read, or nothing yet. */
export type Shown = ResultRead | { state: "loading" };

/** The status the result route answers each state with; the browser checks the two agree. */
export const RESULT_STATUS: Record<ResultRead["state"], number> = {
  ready: 200,
  pending: 409,
  withheld: 409,
  no_result: 404,
  not_found: 404,
  expired: 410,
  unavailable: 503,
  signed_out: 401,
};

/**
 * What the browser makes of the route's answer. A redirect is the session middleware sending the
 * fetch to /login: signed out, whatever the body. Anything that is not exactly a state this route
 * answers, with its own status, is "unavailable" — never content.
 */
export function clientResultRead(
  response: { status: number; redirected: boolean; json: boolean },
  body: unknown,
): ResultRead {
  if (response.redirected || response.status === 401) return { state: "signed_out" };
  if (!response.json || typeof body !== "object" || body === null) return { state: "unavailable" };
  const state = (body as { state?: unknown }).state;
  if (typeof state !== "string" || !Object.hasOwn(RESULT_STATUS, state)) return { state: "unavailable" };
  if (RESULT_STATUS[state as ResultRead["state"]] !== response.status) return { state: "unavailable" };
  if (state === "ready") {
    const text = (body as { text?: unknown }).text;
    return typeof text === "string" ? { state, text } : { state: "unavailable" };
  }
  return { state } as ResultRead;
}

/** One read of the no-store result route; `null` when the read was abandoned (navigation). */
export async function readResult(
  requestId: string,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<ResultRead | null> {
  try {
    const response = await fetcher(`/usage/${encodeURIComponent(requestId)}/result`, {
      cache: "no-store",
      credentials: "same-origin",
      signal,
    });
    const json = (response.headers.get("content-type") ?? "").startsWith("application/json");
    const body: unknown = json ? await response.json() : null;
    return clientResultRead({ status: response.status, redirected: response.redirected, json }, body);
  } catch {
    return signal?.aborted ? null : { state: "unavailable" };
  }
}

/**
 * Reads the result now, and again when the page is restored from the back/forward cache — hiding
 * the old content first, so content that expired meanwhile is never shown from memory. The answer
 * aborts the reads and stops listening (navigation away); a read answering after that shows nothing.
 */
export function watchResult(
  read: (signal: AbortSignal) => Promise<ResultRead | null>,
  show: (shown: Shown) => void,
  page: Pick<EventTarget, "addEventListener" | "removeEventListener">,
): () => void {
  const controller = new AbortController();
  const load = () => {
    read(controller.signal).then((answer) => {
      if (answer !== null && !controller.signal.aborted) show(answer);
    });
  };
  const onShow = (event: Event) => {
    if ((event as Event & { persisted?: boolean }).persisted) {
      show({ state: "loading" });
      load();
    }
  };
  load();
  page.addEventListener("pageshow", onShow);
  return () => {
    controller.abort();
    page.removeEventListener("pageshow", onShow);
  };
}

/** setTimeout's ceiling; a longer wait is re-armed when it fires. */
const MAX_TIMER_MS = 2 ** 31 - 1;

/** Milliseconds until content must be dropped (0 = now); an unreadable expiry is now. */
export function expiryDelayMs(expiresAt: string, nowMs: number): number {
  const at = Date.parse(expiresAt);
  if (Number.isNaN(at)) return 0;
  return Math.min(Math.max(at - nowMs, 0), MAX_TIMER_MS);
}

/** Calls `onExpire` once `now` reaches the persisted expiry, re-arming past the timer ceiling. */
export function watchExpiry(expiresAt: string, now: () => number, schedule: Schedule, onExpire: () => void): () => void {
  let cancel = () => {};
  const arm = () => {
    const delay = expiryDelayMs(expiresAt, now());
    if (delay === 0) return onExpire();
    cancel = schedule(arm, delay);
  };
  arm();
  return () => cancel();
}

// ---------------------------------------------------------------------------
// Retry guidance: the page never resubmits
// ---------------------------------------------------------------------------

export const RETRY_GUIDANCE =
  "Buttons on this page only re-read this request; none of them runs it again. Sending it again from " +
  "your client is a new request and is charged separately. Resending with the same Idempotency-Key, the " +
  "same body and the same mode (synchronous, streaming or async) within 24 hours of it finishing answers " +
  "with this request instead of starting a new one.";
