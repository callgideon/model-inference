/**
 * V1 — URL state for the trace list: parse → validate against allowlists → typed `TraceQuery`.
 *
 * Nothing a caller types reaches `ConsoleServices.traces` unchecked. A query parameter is either
 * recognised *and* inside its vocabulary, or it is dropped and reported: silently forwarding a
 * value would hand the service a filter the contract refuses (`invalid_request`), and silently
 * ignoring one returns a page that looks right and teaches the reader nothing (08 §9, R17).
 *
 * Pure and clock-injected so `tests/v` can run it under `node --test` (R48: relative `.ts`
 * imports, no `@/`, no `.tsx`).
 */

import {
  DEFAULT_PAGE_LIMIT,
  JOB_STATES,
  MAX_PAGE_LIMIT,
  type JobState,
  type TraceQuery,
} from "../../../lib/contracts/types.ts";
import { RANGES, type RangeKey } from "../usage/ranges.ts";

/** Next's `searchParams`, before anything has been checked. */
export type RawParams = Record<string, string | string[] | undefined>;

/**
 * The content states a *list* filter may name. `off` is deliberately absent: an off-mode request
 * has no trace row at all (R13), so `?content=off` would return an empty page that reads as "no
 * traffic" instead of "those requests are not traced". It is refused and explained.
 */
export const CONTENT_FILTERS = ["available", "metadata_only", "pending", "lost", "expired"] as const;
export type ContentFilter = (typeof CONTENT_FILTERS)[number];

/** Capture modes that can own a trace row; `off` is excluded for the same reason (R13). */
export const MODE_FILTERS = ["minimal", "full"] as const;
export type ModeFilter = (typeof MODE_FILTERS)[number];

export const FEEDBACK_FILTERS = ["any", "yes", "no"] as const;
export type FeedbackFilter = (typeof FEEDBACK_FILTERS)[number];

/** Page sizes offered, all within the contract's hard `MAX_PAGE_LIMIT` (never a clamp of input). */
export const PAGE_SIZES = [DEFAULT_PAGE_LIMIT, 50, MAX_PAGE_LIMIT] as const;

/**
 * An identifier filter is an identifier, not a document. 200 is the same number the service's own
 * filter check uses, counted here in code points (R54); the allowlist below is what makes the two
 * counts agree rather than an assumption about how the service counts.
 */
export const MAX_FILTER_CHARS = 200;

/**
 * A cursor is opaque (R36): it is length- and charset-checked so a hostile URL cannot post a
 * document, and otherwise passed through exactly as minted. The charset covers base64url, padding
 * and the dot separators a signed or encrypted cursor uses, because C mints its own (README:
 * "C should sign or encrypt its cursors").
 */
export const MAX_CURSOR_CHARS = 1024;
const CURSOR_CHARS = /^[A-Za-z0-9._~+/=-]+$/;

/**
 * A model id is an identifier, and this one is echoed back into the page and the filter control.
 * The allowlist is the shape the served ids have (`marlin-2b@2026-09-01`), so a C1 control
 * character (U+0085), a line separator (U+2028/9) or a bidi override (U+202E) cannot ride into the
 * query or the rendered text — a `[\u0000-\u001f\u007f]` check let all three through.
 *
 * The length bound counts Unicode code points, as R54 requires of every shared character bound.
 * With this allowlist the units question cannot arise (every allowed character is one UTF-16 unit),
 * which is deliberate: the fake's own bound is written in `String.length`, so a value where the two
 * disagree must never be constructible here. See the integration note in the V1 evidence.
 */
const MODEL_ID = /^[A-Za-z0-9._:@/+-]+$/;

const RFC3339_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

/**
 * How far back a window anchor may reach: the 13 calendar months of trace metadata the platform
 * keeps (08 §5 `TRACE_METADATA_MONTHS`), taken generously as 403 days so a legitimate anchor at the
 * far edge is never refused. Beyond it there is nothing to page through, and the `from` a longer
 * reach computes stops being a timestamp the service can parse at all.
 */
export const MAX_ANCHOR_AGE_MS = 403 * 24 * 60 * 60 * 1000;

/** Forward tolerance for a clock a few minutes ahead of this one; not a window into the future. */
export const MAX_ANCHOR_SKEW_MS = 5 * 60 * 1000;

/** Every parameter name this page understands. Anything else is ignored and reported. */
export const TRACE_PARAMS = [
  "range",
  "at",
  "key",
  "model",
  "state",
  "content",
  "mode",
  "feedback",
  "size",
  "cursor",
] as const;

export const DEFAULT_RANGE: RangeKey = "24h";

export type FilterState = {
  range: RangeKey;
  /** The resolved window anchor, always set; `pinned` says whether the URL asked for it. */
  at: string;
  pinned: boolean;
  key: string | null;
  model: string | null;
  state: JobState | null;
  content: ContentFilter | null;
  mode: ModeFilter | null;
  feedback: FeedbackFilter;
  size: number;
  cursor: string | null;
};

export type RejectedParam = { name: string; why: string };

export type ParsedTraceParams = {
  filters: FilterState;
  /** Only ever `TRACE_QUERY_FIELDS`; safe to hand to `ConsoleServices.traces`. */
  query: TraceQuery;
  /** Recognised names whose value was refused, with the reason to show the reader. */
  rejected: RejectedParam[];
  /** Unrecognised names, which did nothing — including any attempt to smuggle `org_id`. */
  ignored: string[];
  /** True when the reader narrowed anything beyond the default window. */
  narrowed: boolean;
};

function resolveWindow(range: RangeKey, anchorMs: number): { from: string; to: string } {
  return {
    from: new Date(anchorMs - RANGES[range]).toISOString(),
    to: new Date(anchorMs).toISOString(),
  };
}

/**
 * One value per name, exactly as written. A repeated parameter is refused rather than resolved by
 * taking the first or the last: the two conventions disagree, and a filter the reader cannot
 * predict is worse than an error they can see. Nothing is trimmed either — `?size=%20100` and
 * `?state=%20failed` are refused rather than quietly fixed, which is the same rule as everywhere
 * else here: a value is what it says or it is reported.
 */
function single(raw: RawParams, name: string, rejected: RejectedParam[]): string | null {
  const value = raw[name];
  if (value === undefined) return null;
  if (Array.isArray(value)) {
    rejected.push({ name, why: "given more than once" });
    return null;
  }
  return value === "" ? null : value;
}

/**
 * The window anchor a page link carries. Strict, because everything downstream trusts it: the
 * `from`/`to` the service receives are computed from it, and an anchor outside the retained range
 * produces a `from` that is not an RFC 3339 timestamp at all.
 */
function parseAnchor(value: string, now: number): { ms: number } | { why: string } {
  if (!RFC3339_UTC.test(value)) {
    return { why: "must be an RFC 3339 UTC timestamp, like 2026-09-21T12:00:00Z" };
  }
  const ms = Date.parse(value);
  // An impossible date (`2026-13-45T99:99:99Z`) is NaN here, and NaN fails both comparisons — so it
  // is refused by the same check as an out-of-range one, rather than reaching a formatter that
  // would throw a RangeError into the render.
  if (!(ms >= now - MAX_ANCHOR_AGE_MS && ms <= now + MAX_ANCHOR_SKEW_MS)) {
    return { why: "is outside the 13 months of trace metadata this console keeps" };
  }
  // `2026-02-30T00:00:00Z` parses as 2 March. Silently showing a window the reader did not ask for
  // is worse than refusing the link, so the instant must survive the round trip unchanged.
  if (new Date(ms).toISOString().slice(0, 19) !== value.slice(0, 19)) {
    return { why: "is not a real date" };
  }
  return { ms };
}

function fromVocabulary<T extends string>(
  name: string,
  allowed: readonly T[],
  value: string | null,
  rejected: RejectedParam[],
  why = `must be one of ${allowed.join(", ")}`,
): T | null {
  if (value === null) return null;
  if ((allowed as readonly string[]).includes(value)) return value as T;
  rejected.push({ name, why });
  return null;
}

function identifier(
  name: string,
  value: string | null,
  rejected: RejectedParam[],
  allowed: readonly string[] | null,
): string | null {
  if (value === null) return null;
  // Code points, per R54: `.length` would let an astral character count twice.
  if ([...value].length > MAX_FILTER_CHARS) {
    rejected.push({ name, why: `must be at most ${MAX_FILTER_CHARS} characters` });
    return null;
  }
  if (!MODEL_ID.test(value)) {
    rejected.push({ name, why: "must be an identifier: letters, digits and . _ : @ / + -" });
    return null;
  }
  if (allowed !== null && !allowed.includes(value)) {
    rejected.push({ name, why: "is not one of this organization's keys" });
    return null;
  }
  return value;
}

/**
 * Parse the URL into the filters the page renders and the typed query the service receives.
 *
 * `keyIds` are the organization's own key ids (from `keys.list`), so a `?key=` naming another
 * tenant's key is refused here rather than turning into an empty page. Tenant isolation itself
 * stays where it belongs — the service binds the tenant to `session.orgId` — this is only the
 * allowlist that keeps a guessable identifier out of the query.
 */
export function parseTraceParams(
  raw: RawParams,
  context: { now: number; keyIds: readonly string[] },
): ParsedTraceParams {
  const rejected: RejectedParam[] = [];
  const ignored = Object.keys(raw)
    .filter((name) => !(TRACE_PARAMS as readonly string[]).includes(name))
    .sort();

  const range =
    fromVocabulary("range", Object.keys(RANGES) as RangeKey[], single(raw, "range", rejected), rejected) ??
    DEFAULT_RANGE;

  const rawAt = single(raw, "at", rejected);
  let anchorMs = context.now;
  let pinned = false;
  if (rawAt !== null) {
    const anchor = parseAnchor(rawAt, context.now);
    if ("ms" in anchor) {
      anchorMs = anchor.ms;
      pinned = true;
    } else {
      rejected.push({ name: "at", why: anchor.why });
    }
  }

  const rawCursor = single(raw, "cursor", rejected);
  let cursor: string | null = null;
  if (rawCursor !== null) {
    if ([...rawCursor].length > MAX_CURSOR_CHARS || !CURSOR_CHARS.test(rawCursor)) {
      rejected.push({ name: "cursor", why: "is not a page cursor this list minted" });
    } else if (!pinned) {
      // The window is relative to an anchor. Walking pages while the anchor moves changes the
      // query the cursor was minted for, which the service answers with `invalid_cursor`. A page
      // link therefore has to pin its window, and `traceHref` always does.
      rejected.push({ name: "cursor", why: "needs the page link's pinned window (at=…)" });
    } else {
      cursor = rawCursor;
    }
  }

  const rawSize = single(raw, "size", rejected);
  let size: number = DEFAULT_PAGE_LIMIT;
  if (rawSize !== null) {
    // One spelling per size: `Number` accepts `1e2`, `0100`, ` 100` and `100\n` as 100, each of
    // which would pass a membership check as a number the offered set does not contain as text.
    const parsed = /^\d+$/.test(rawSize) && String(Number(rawSize)) === rawSize ? Number(rawSize) : Number.NaN;
    if ((PAGE_SIZES as readonly number[]).includes(parsed)) size = parsed;
    else rejected.push({ name: "size", why: `must be one of ${PAGE_SIZES.join(", ")}` });
  }

  const filters: FilterState = {
    range,
    at: new Date(anchorMs).toISOString(),
    pinned,
    key: identifier("key", single(raw, "key", rejected), rejected, context.keyIds ?? null),
    model: identifier("model", single(raw, "model", rejected), rejected, null),
    state: fromVocabulary("state", JOB_STATES, single(raw, "state", rejected), rejected),
    content: fromVocabulary(
      "content",
      CONTENT_FILTERS,
      single(raw, "content", rejected),
      rejected,
      `must be one of ${CONTENT_FILTERS.join(", ")} — requests with tracing off have no trace row, so they are listed under Usage instead`,
    ),
    mode: fromVocabulary(
      "mode",
      MODE_FILTERS,
      single(raw, "mode", rejected),
      rejected,
      "must be minimal or full — a request with tracing off has no trace row",
    ),
    feedback: fromVocabulary("feedback", FEEDBACK_FILTERS, single(raw, "feedback", rejected), rejected) ?? "any",
    size,
    cursor,
  };

  const window = resolveWindow(filters.range, anchorMs);
  const query: TraceQuery = {
    limit: filters.size,
    from: window.from,
    to: window.to,
    ...(filters.key === null ? {} : { key_id: filters.key }),
    ...(filters.model === null ? {} : { model: filters.model }),
    ...(filters.state === null ? {} : { job_state: filters.state }),
    ...(filters.mode === null ? {} : { trace_mode: filters.mode }),
    ...(filters.content === null ? {} : { content: filters.content }),
    ...(filters.feedback === "any" ? {} : { has_feedback: filters.feedback === "yes" }),
    ...(filters.cursor === null ? {} : { cursor: filters.cursor }),
  };

  return {
    filters,
    query,
    rejected,
    ignored,
    narrowed:
      filters.key !== null ||
      filters.model !== null ||
      filters.state !== null ||
      filters.content !== null ||
      filters.mode !== null ||
      filters.feedback !== "any" ||
      filters.range !== DEFAULT_RANGE,
  };
}

/** The filter-bearing keys of `FilterState`; changing any of them invalidates a cursor. */
const FILTER_KEYS = ["range", "key", "model", "state", "content", "mode", "feedback", "size"] as const;

/**
 * Apply a filter change.
 *
 * A cursor is bound to its query scope, so any change to a filter drops it — keeping it would
 * resume a walk that no longer exists (the fake answers `invalid_cursor`; so must C).
 */
export function withPatch(filters: FilterState, patch: Partial<FilterState>): FilterState {
  const changesFilter = FILTER_KEYS.some(
    (key) => patch[key] !== undefined && patch[key] !== filters[key],
  );
  // `"cursor" in patch` rather than `patch.cursor ?? …`: an explicit `cursor: null` is how a caller
  // says "leave the walk", and `??` cannot express it — it falls through to the cursor already
  // there. That was a real dead end: the way back from a bad cursor asked for `cursor: null`, got
  // the bad cursor again because no *filter* had changed, and rendered a link to the very page the
  // reader was stuck on.
  const cursor = changesFilter ? null : "cursor" in patch ? (patch.cursor ?? null) : filters.cursor;
  return {
    ...filters,
    ...patch,
    cursor,
    // A filter change starts a new walk, so it also releases the pinned window: the reader asked for
    // "the last 24 hours", not "the last 24 hours as of whenever this link was made". A caller
    // leaving a walk another way — the way back from a bad cursor — passes `pinned: false` itself,
    // because `at` exists only to hold a walk's window still.
    pinned: changesFilter ? false : ("pinned" in patch ? (patch.pinned ?? false) : filters.pinned),
  };
}

/**
 * The URL for a filter change or the next page. `at` is written only when it matters: pinned by the
 * reader, or carried by a page link so the window cannot slide under the walk.
 */
export function traceHref(filters: FilterState, patch: Partial<FilterState> = {}): string {
  const next = withPatch(filters, patch);
  const params = new URLSearchParams();
  if (next.range !== DEFAULT_RANGE) params.set("range", next.range);
  if (next.key !== null) params.set("key", next.key);
  if (next.model !== null) params.set("model", next.model);
  if (next.state !== null) params.set("state", next.state);
  if (next.content !== null) params.set("content", next.content);
  if (next.mode !== null) params.set("mode", next.mode);
  if (next.feedback !== "any") params.set("feedback", next.feedback);
  if (next.size !== DEFAULT_PAGE_LIMIT) params.set("size", String(next.size));
  if (next.cursor !== null) {
    params.set("at", next.at);
    params.set("cursor", next.cursor);
  } else if (next.pinned) {
    params.set("at", next.at);
  }
  const query = params.toString();
  return query === "" ? "/traces" : `/traces?${query}`;
}
