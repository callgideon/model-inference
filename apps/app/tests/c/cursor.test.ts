// node --test "tests/**/*.test.ts"
//
// Cursors are opaque to the contract (R36), so the exported suite can only forge them as a black box.
// These are the shape-aware forgeries that belong to the implementation: a caller who knows the format
// still cannot mint a cursor, move one between tenants, filters or lists, or edit the key inside one.

import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import { cursorScope, decodeCursor, encodeCursor } from "../../lib/services/cursor.ts";

/**
 * The tag the service would produce for a given payload, re-derived here with the known secret.
 *
 * Without it the "malformed payload" cases were worthless: they carried a tag that was not a MAC of
 * the body, so every one of them died on the signature and the shape checks they claimed to cover
 * were never reached — removing those checks left the suite green.
 */
function realTag(secret: string, scope: string, payload: string): string {
  return createHmac("sha256", secret)
    .update(`${scope}\u0000${payload}`)
    .digest("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function signed(secret: string, scope: string, body: string): string {
  const payload = Buffer.from(body, "utf8").toString("base64url");
  return `${payload}.${realTag(secret, scope, payload)}`;
}

const SECRET = "c1-test-cursor-secret-0123456789";
const OTHER_SECRET = "another-secret-0123456789012345";
const KEY = { at: "2026-09-20T12:00:00.000Z", id: "9f8e7d6c" };

const scope = (orgId: string, operation: string, filters: Record<string, unknown> = {}) =>
  cursorScope(orgId, operation, filters);

test("a cursor round-trips only under its own scope and secret", () => {
  const mine = scope("org-a", "usage");
  const cursor = encodeCursor(SECRET, mine, KEY);
  assert.deepEqual(decodeCursor(SECRET, mine, cursor), KEY);
  assert.equal(decodeCursor(SECRET, scope("org-b", "usage"), cursor), null, "another tenant");
  assert.equal(decodeCursor(SECRET, scope("org-a", "traces"), cursor), null, "another list");
  assert.equal(decodeCursor(SECRET, scope("org-a", "usage", { model: "m1" }), cursor), null, "another filter");
  assert.equal(decodeCursor(OTHER_SECRET, mine, cursor), null, "another signing key");
});

test("the page size is not part of the scope, so it may change mid-walk", () => {
  assert.equal(scope("org-a", "usage", { limit: 7, cursor: "x" }), scope("org-a", "usage", { limit: 100 }));
  // And a filter is, whatever order the caller wrote its fields in.
  assert.equal(
    scope("org-a", "usage", { model: "m", key_id: "k" }),
    scope("org-a", "usage", { key_id: "k", model: "m" }),
  );
  assert.notEqual(scope("org-a", "usage", { model: "m" }), scope("org-a", "usage", { model: "n" }));
  assert.equal(scope("org-a", "usage", { model: undefined }), scope("org-a", "usage"), "absent is absent");
});

test("a caller who knows the format still cannot mint or edit a cursor", () => {
  const mine = scope("org-a", "usage");
  const cursor = encodeCursor(SECRET, mine, KEY);
  const [payload, tag] = cursor.split(".");

  // Hand-built payload with the right shape and no valid tag.
  const forged = Buffer.from(JSON.stringify(["2000-01-01T00:00:00.000Z", "aaaa"]), "utf8").toString("base64url");
  assert.equal(decodeCursor(SECRET, mine, `${forged}.${tag}`), null, "another row's key under a stolen tag");
  assert.equal(decodeCursor(SECRET, mine, forged), null, "no tag at all");
  assert.equal(decodeCursor(SECRET, mine, `${forged}.`), null, "an empty tag");
  assert.equal(decodeCursor(SECRET, mine, `.${tag}`), null, "no payload");
  assert.equal(decodeCursor(SECRET, mine, `${payload}.${tag.slice(0, -1)}a`), null, "a tag with one character changed");
  assert.equal(decodeCursor(SECRET, mine, ""), null, "empty");
  assert.equal(decodeCursor(SECRET, mine, `${payload}.${tag}`.repeat(20)), null, "an oversized cursor");
  assert.equal(decodeCursor(SECRET, mine, `not-base64.${tag}`), null, "a payload that is not a key");

  // A payload that really does authenticate, and still is not a two-element key of strings. These are
  // signed with the service's own construction, so the shape validation is what has to refuse them.
  for (const rubbish of ["{}", "[]", '["only-one"]', '["", "b"]', '["a", ""]', "[1, 2]", '["a", "b", "c"]', '"a"', "null"]) {
    const cursor = signed(SECRET, mine, rubbish);
    assert.notEqual(decodeCursor(SECRET, mine, cursor), undefined);
    assert.equal(decodeCursor(SECRET, mine, cursor), null, `${rubbish} authenticates, so its shape must refuse it`);
  }
  // The construction is right: the same helper, over a well-formed body, is accepted.
  assert.deepEqual(decodeCursor(SECRET, mine, signed(SECRET, mine, JSON.stringify([KEY.at, KEY.id]))), KEY);
});

test("an over-long cursor is refused before it is hashed, even when it authenticates", () => {
  const mine = scope("org-a", "usage");
  // A genuine cursor of the service's own making, but far past the bound: the length check is what
  // refuses it, so removing that check cannot pass unnoticed.
  const huge = encodeCursor(SECRET, mine, { at: KEY.at, id: "x".repeat(4000) });
  assert.ok(huge.length > 512, "the probe must actually be over-long");
  assert.equal(decodeCursor(SECRET, mine, huge), null, "an over-long cursor is refused");
  const justInside = encodeCursor(SECRET, mine, { at: KEY.at, id: "y".repeat(300) });
  assert.ok(justInside.length <= 512);
  assert.deepEqual(decodeCursor(SECRET, mine, justInside)?.id, "y".repeat(300), "and one inside the bound works");
});

test("a cursor carries no secret and no readable tenant", () => {
  const cursor = encodeCursor(SECRET, scope("org-a", "usage"), KEY);
  assert.ok(!cursor.includes(SECRET), "the signing key never appears in a cursor");
  const decoded = Buffer.from(cursor.split(".")[0], "base64url").toString("utf8");
  assert.ok(!decoded.includes("org-a"), "the scope is authenticated, not carried");
  assert.equal(decoded, JSON.stringify([KEY.at, KEY.id]), "only the row's own sort key is inside");
});
