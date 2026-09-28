/**
 * V1M — URL state for the request list (moved from the App's trace list, V1).
 *
 * The provider trace read (R176) takes no filters: its only page state is the cursor it mints (R36), so
 * that is the only parameter this page understands. Everything else is dropped and reported, never
 * forwarded; in particular the provider is always the session workspace, never a URL value.
 *
 * Pure, so `tests/v/list` runs it under `node --test` (relative `.ts` imports, no `@/`).
 */

/** Next's `searchParams`, before anything has been checked. */
export type RawParams = Record<string, string | string[] | undefined>;
export type RejectedParam = { name: string; why: string };
export type ListParams = { cursor: string | null; rejected: RejectedParam[]; ignored: string[] };

/**
 * A cursor is opaque: length- and charset-checked so a hostile URL cannot post a document, and otherwise
 * passed through exactly as minted. The route mints unpadded base64url.
 */
export const MAX_CURSOR_CHARS = 1024;
const CURSOR = /^[A-Za-z0-9_-]+$/;
const NOT_OURS = "is not a page link this list made";

export function parseListParams(raw: RawParams): ListParams {
  const ignored = Object.keys(raw).filter((name) => name !== "cursor").sort();
  const value = raw.cursor;
  // One value per name, exactly as written: a repeated parameter is refused rather than guessed at.
  if (Array.isArray(value)) return { cursor: null, rejected: [{ name: "cursor", why: "given more than once" }], ignored };
  if (value === undefined || value === "") return { cursor: null, rejected: [], ignored };
  if (value.length > MAX_CURSOR_CHARS || !CURSOR.test(value)) return { cursor: null, rejected: [{ name: "cursor", why: NOT_OURS }], ignored };
  return { cursor: value, rejected: [], ignored };
}

export function listHref(cursor: string | null): string {
  return cursor === null ? "/requests" : `/requests?cursor=${encodeURIComponent(cursor)}`;
}
