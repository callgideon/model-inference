// node --test "tests/**/*.test.ts"
//
// Cursors are opaque to the contract (R36), so the exported suite can only forge them as a black box.
// These are the shape-aware forgeries that belong to the implementation: a caller who knows the format
// still cannot mint a cursor, move one between tenants, filters or lists, or edit the key inside one.

import assert from "node:assert/strict";
import test from "node:test";
import { cursorScope, decodeCursor, encodeCursor } from "../../lib/services/cursor.ts";

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

  // A payload that authenticates but is not a two-element key of strings.
  for (const rubbish of ["{}", "[]", '["only-one"]', '["", "b"]', '[1, 2]', '["a", "b", "c"]']) {
    const body = Buffer.from(rubbish, "utf8").toString("base64url");
    assert.equal(
      decodeCursor(SECRET, mine, `${body}.${Buffer.from(encodeCursor(SECRET, mine, KEY)).toString("base64url")}`),
      null,
      `${rubbish} must not decode`,
    );
  }
});

test("a cursor carries no secret and no readable tenant", () => {
  const cursor = encodeCursor(SECRET, scope("org-a", "usage"), KEY);
  assert.ok(!cursor.includes(SECRET), "the signing key never appears in a cursor");
  const decoded = Buffer.from(cursor.split(".")[0], "base64url").toString("utf8");
  assert.ok(!decoded.includes("org-a"), "the scope is authenticated, not carried");
  assert.equal(decoded, JSON.stringify([KEY.at, KEY.id]), "only the row's own sort key is inside");
});
