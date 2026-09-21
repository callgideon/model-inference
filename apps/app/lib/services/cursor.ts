/**
 * Opaque, authenticated keyset cursors (C1).
 *
 * A cursor carries the row's sort key — `(created_at, id)`, or `(name, org_id)` for the operator
 * list — and a MAC over that key **and the query scope**: tenant, operation and filters, but not
 * `limit` (the contract lets the page size change mid-walk). Authentication is what makes the four
 * black-box forgeries of R36 fail: a cursor from another list, another tenant or another filter set
 * verifies against a different scope, and a disturbed character breaks the MAC.
 *
 * The secret is passed in; only the server-only module reads it from the environment, and it is
 * read by name and never logged, embedded in a cursor or returned in an error.
 */

import { createHmac, timingSafeEqual } from "node:crypto";
import type { Keyset } from "./query.ts";

/** Cursor payloads are tiny; anything larger than this is refused before any hashing. */
const MAX_CURSOR_CHARS = 512;

function base64url(value: Buffer): string {
  return value.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function mac(secret: string, scope: string, payload: string): string {
  return base64url(createHmac("sha256", secret).update(`${scope}\u0000${payload}`).digest());
}

/**
 * The query a cursor belongs to. Filters are sorted and JSON-encoded so the same query produces
 * the same scope whatever order the caller wrote its fields in, and `undefined` counts as absent.
 */
export function cursorScope(orgId: string, operation: string, filters: Record<string, unknown>): string {
  const entries = Object.entries(filters)
    .filter(([name, value]) => name !== "cursor" && name !== "limit" && value !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `${orgId}|${operation}|${JSON.stringify(entries)}`;
}

export function encodeCursor(secret: string, scope: string, key: Keyset): string {
  const payload = base64url(Buffer.from(JSON.stringify([key.at, key.id]), "utf8"));
  return `${payload}.${mac(secret, scope, payload)}`;
}

/** The key this service minted for this exact scope, or null — which callers turn into `invalid_cursor`. */
export function decodeCursor(secret: string, scope: string, cursor: string): Keyset | null {
  if (typeof cursor !== "string" || cursor.length === 0 || cursor.length > MAX_CURSOR_CHARS) return null;
  const dot = cursor.indexOf(".");
  if (dot <= 0 || dot === cursor.length - 1) return null;
  const payload = cursor.slice(0, dot);
  const presented = Buffer.from(cursor.slice(dot + 1), "utf8");
  const expected = Buffer.from(mac(secret, scope, payload), "utf8");
  // Constant-time: a cursor is authenticated data, and a length check first keeps
  // `timingSafeEqual` from throwing on a forgery of the wrong size.
  if (presented.length !== expected.length || !timingSafeEqual(presented, expected)) return null;
  try {
    const decoded: unknown = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (!Array.isArray(decoded) || decoded.length !== 2) return null;
    const [at, id] = decoded as unknown[];
    if (typeof at !== "string" || at === "" || typeof id !== "string" || id === "") return null;
    return { at, id };
  } catch {
    return null;
  }
}
