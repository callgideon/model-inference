// UX-01 (UX-T01, audit UX-A01/A10): the live catalog said "physically deleted within 0 hours" because the
// public /v1/models answers `physical_deletion_bound_s: null` (no committed bound, P-25) and the Docs
// copy only checked `!== undefined`. These cases cross the wire-to-view boundary: a payload shaped like
// the live one goes through loadCatalog's fetch and parser into the Docs' retention copy; none calls a
// formatter with a cast value. Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { allCopy, retentionFacts } from "../../app/(console)/docs/content.ts";
import { loadCatalog, priceView, type Catalog } from "../../app/(console)/models/catalog.ts";

const DIR = new URL("../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const CREDIT_DOC = JSON.parse(readFileSync(new URL("published_marlin_credit.json", DIR), "utf8"));
const BASE = "https://api.example.test";
const UNKNOWN = "The result access window does not specify a physical-deletion deadline.";

/** The catalog as the gateway serves it, the bound set to `bound` (`undefined`: the key is absent). */
async function served(bound: unknown): Promise<Catalog> {
  const record = structuredClone(CREDIT_DOC);
  if (bound !== undefined) record.retention.physical_deletion_bound_s = bound;
  // The bytes a client receives: JSON.stringify keeps an explicit null, exactly as the live endpoint did.
  const body = JSON.stringify({ object: "list", data: [record] });
  return loadCatalog(BASE, async () => new Response(body, { status: 200, headers: { "content-type": "application/json" } }));
}

async function docsRetention(bound: unknown): Promise<string> {
  const catalog = await served(bound);
  assert.equal(catalog.status, "ok", "the catalog stays available");
  if (catalog.status !== "ok") return "";
  return retentionFacts(catalog.models[0].retention).join("\n");
}

test("UXR-01 the live null bound never renders a deletion deadline: no 0 hours, the unknown is said", async () => {
  const facts = await docsRetention(null);
  assert.doesNotMatch(facts, /0 hours|0 seconds|within 0/, "no zero-hour deletion promise");
  assert.doesNotMatch(facts, /physically deleted within/, "no deletion deadline at all");
  assert.ok(facts.includes(UNKNOWN), "the unknown bound is stated as unknown");
});

test("UXR-02 an absent bound reads the same as null (Python's `Count | None = None`)", async () => {
  const facts = await docsRetention(undefined);
  assert.doesNotMatch(facts, /physically deleted within/);
  assert.ok(facts.includes(UNKNOWN));
});

test("UXR-03 a committed positive bound is stated, and only then", async () => {
  const facts = await docsRetention(172800);
  assert.match(facts, /Expired content is physically deleted within 2 days\./);
  assert.ok(!facts.includes(UNKNOWN), "a stated bound is not also called unspecified");
});

test("UXR-04 the other lifetimes stay the record's with an unknown bound: 24 h result, 1 h stream, 24 h key, 7 d cache", async () => {
  const facts = await docsRetention(null);
  assert.match(facts, /A result stays readable for 24 hours after the request finishes; after that GET \/v1\/jobs\/\{handle\}\/result answers 410 result_expired\./);
  assert.match(facts, /replayed from Last-Event-ID for 1 hour\./);
  assert.match(facts, /keeps answering with its original job for 24 hours after the job finishes\./);
  assert.match(facts, /processing cache for up to 7 days\./);
  assert.match(facts, /Turning it off does not delete the serving data above/);
});

test("UXR-05 a zero or malformed bound fails safely: the catalog is unavailable, never a figure", async () => {
  for (const bound of [0, -3600, 1.5, "86400", true, {}]) {
    assert.deepEqual(await served(bound), { status: "unavailable", reason: "malformed" }, JSON.stringify(bound));
  }
});

test("UXR-06 the copy guard covers the unknown sentence: every renderable sentence for a live-shaped record", async () => {
  const catalog = await served(null);
  assert.equal(catalog.status, "ok");
  if (catalog.status !== "ok") return;
  const copy = allCopy(catalog.models[0], priceView(catalog.models[0]));
  assert.ok(copy.includes(UNKNOWN));
  assert.ok(!copy.some((line) => /physically deleted within|within 0 /.test(line)));
});

const PANEL = readFileSync(new URL("../../app/(console)/usage/[requestId]/result-panel.tsx", import.meta.url), "utf8");

test("UXR-07 an expired result says it is no longer available, never that its content was removed or deleted", () => {
  const expired = /expired: "([^"]+)"/.exec(PANEL)?.[1] ?? "";
  assert.equal(expired, "This result is no longer available. Request status and usage remain available.");
  assert.doesNotMatch(expired, /removed|deleted|purged|erased/i, "read expiry is not physical deletion");
});
