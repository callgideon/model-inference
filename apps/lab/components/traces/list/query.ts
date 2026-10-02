/**
 * URL state for the request list: the route's own opaque cursor (R36) and its two server-side filters
 * (lab_traces.py FILTERS, AP-07c). Everything else is dropped and reported, never forwarded; in
 * particular the provider is always the session workspace, never a URL value. Nothing here filters a
 * page: a filter is sent to the route, which narrows the serving versions before it reads.
 *
 * Pure, so it runs under `node --test` (relative `.ts` imports, no `@/`).
 */
import { NO_FILTER, type TraceFilter } from "../detail/port.ts";

/** Next's `searchParams`, before anything has been checked. */
export type RawParams = Record<string, string | string[] | undefined>;
export type RejectedParam = { name: string; why: string };
export type ListParams = { cursor: string | null; filter: TraceFilter; rejected: RejectedParam[]; ignored: string[] };

/**
 * A cursor is opaque: length- and charset-checked so a hostile URL cannot post a document, and otherwise
 * passed through exactly as minted. The route mints unpadded base64url, and a filtered list's cursor
 * adds `.<digest>`.
 */
export const MAX_CURSOR_CHARS = 1024;
const CURSOR = /^[A-Za-z0-9_-]+(\.[0-9a-f]+)?$/;
/** A filter value is an identifier: bounded, otherwise sent as written (URL-encoded). */
export const MAX_FILTER_CHARS = 200;
const FILTERS = ["model_id", "serving_version_id"] as const;
const KNOWN = new Set<string>(["cursor", ...FILTERS]);
const NOT_OURS = "is not a page link this list made";

/** One value per name, exactly as written: a repeated parameter is refused rather than guessed at. */
function one(raw: RawParams, name: string, max: number, rejected: RejectedParam[]): string | null {
  const value = raw[name];
  if (Array.isArray(value)) rejected.push({ name, why: "given more than once" });
  else if (value === undefined || value === "") return null;
  else if (value.length > max) rejected.push({ name, why: `is longer than ${max} characters` });
  else return value;
  return null;
}

export function parseListParams(raw: RawParams): ListParams {
  const ignored = Object.keys(raw).filter((name) => !KNOWN.has(name)).sort();
  const rejected: RejectedParam[] = [];
  let cursor = one(raw, "cursor", Infinity, rejected);
  if (cursor !== null && (cursor.length > MAX_CURSOR_CHARS || !CURSOR.test(cursor))) {
    rejected.push({ name: "cursor", why: NOT_OURS });
    cursor = null;
  }
  const filter = { model_id: one(raw, "model_id", MAX_FILTER_CHARS, rejected), serving_version_id: one(raw, "serving_version_id", MAX_FILTER_CHARS, rejected) };
  return { cursor, filter, rejected, ignored };
}

/** The list at `cursor` under `filter`; the first page carries no cursor. */
export function listHref(cursor: string | null, filter: TraceFilter = NO_FILTER): string {
  const query = new URLSearchParams();
  for (const name of FILTERS) if (filter[name] !== null) query.set(name, filter[name]);
  if (cursor !== null) query.set("cursor", cursor);
  const search = query.toString();
  return search === "" ? "/requests" : `/requests?${search}`;
}
