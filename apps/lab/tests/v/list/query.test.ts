// V1M: the request list's URL state: the route's own opaque cursor (R36) and, since AP-07c, its two
// server-side filters (UX-05 covers those: tests/ux/requests); anything else is reported as ignored,
// never forwarded (a provider id in the URL included).
import assert from "node:assert/strict";
import test from "node:test";
import { listHref, MAX_CURSOR_CHARS, parseListParams } from "../../../components/traces/list/query.ts";

const filter = { model_id: null, serving_version_id: null };

test("V1M-Q01 the route's cursor passes through exactly; every other name is ignored and reported, sorted", () => {
  const cursor = "WyIyMDI2LTA5LTI3VDEwOjAwOjAwKzAwOjAwIiwic2VnLTAwMDE6MCJd";
  assert.deepEqual(parseListParams({ cursor }), { cursor, filter, rejected: [], ignored: [] });
  assert.deepEqual(parseListParams({ provider_org_id: "b", org_id: "x", range: "7d" }), { cursor: null, filter, rejected: [], ignored: ["org_id", "provider_org_id", "range"] });
  assert.deepEqual(parseListParams({ cursor: "" }), { cursor: null, filter, rejected: [], ignored: [] });
});

test("V1M-Q02 a repeated, oversized or non-base64url cursor is refused with its reason, never sent", () => {
  const refused = (raw: Record<string, string | string[]>) => parseListParams(raw).rejected.map((r) => r.why);
  assert.deepEqual(refused({ cursor: ["a", "b"] }), ["given more than once"]);
  assert.deepEqual(refused({ cursor: "a".repeat(MAX_CURSOR_CHARS + 1) }), ["is not a page link this list made"]);
  assert.equal(parseListParams({ cursor: "a".repeat(MAX_CURSOR_CHARS) }).cursor, "a".repeat(MAX_CURSOR_CHARS));
  for (const bad of ["a b", "a/b", "a+b", "a=", "x'--", "é"]) assert.deepEqual(parseListParams({ cursor: bad }), { cursor: null, filter, rejected: [{ name: "cursor", why: "is not a page link this list made" }], ignored: [] }, bad);
});

test("V1M-Q03 page links are /requests plus the cursor, and the first page carries none", () => {
  assert.equal(listHref(null), "/requests");
  assert.equal(listHref("Ab_-9"), "/requests?cursor=Ab_-9");
  assert.equal(listHref("a+b/c"), "/requests?cursor=a%2Bb%2Fc");
});
