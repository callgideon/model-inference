// node --test "tests/**/*.test.ts"
//
// A3 (RV-01, APP-JOURNEY): the Docs/Models copy states what the record and the gateway state, the
// decided disclosures verbatim, and none of the stale claims. The pages themselves are `.tsx`, which
// `node --test` cannot load, so their SOURCE is read and held to the same rules.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  allCopy,
  bytes,
  chargeDisclosure,
  duration,
  ERROR_ROWS,
  plainAmount,
  REVOCATION_COPY,
  retentionFacts,
  ROUTE_ROWS,
  videoFacts,
} from "../../app/(console)/docs/content.ts";
import { priceView } from "../../app/(console)/models/catalog.ts";
import type { Credit } from "../../lib/contracts/v2/money-units.ts";
import { parsePublishedModel } from "../../lib/contracts/v2/published-model.ts";

const DIR = new URL("../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const CREDIT_DOC = JSON.parse(readFileSync(new URL("published_marlin_credit.json", DIR), "utf8"));
const MODEL = parsePublishedModel(structuredClone(CREDIT_DOC));
const PRICE = priceView(MODEL);
const ENVELOPES = JSON.parse(
  readFileSync(new URL("../../../infrx-api/infrx/contracts/fixtures/v1/error_envelopes.json", import.meta.url), "utf8"),
) as Record<string, { http_status: number }>;
const ENDPOINT_DOC = readFileSync(new URL("../../../../research/plan/evidence/e/E4B-endpoint.md", import.meta.url), "utf8");
const PAGES = ["app/(console)/models/page.tsx", "app/(console)/docs/page.tsx"].map((p) => ({
  path: p,
  source: readFileSync(new URL(`../../${p}`, import.meta.url), "utf8"),
}));

/** RV-01's stale claims, and the unit mix-ups 02-credits forbids. */
const BANNED: [RegExp, string][] = [
  [/\b120\s*(s\b|sec|seconds|-second)/i, "the stale 120-second limit"],
  [/two[- ]minute/i, "the stale two-minute limit"],
  [/never stor/i, "a never-stores-content claim"],
  [/(?<!not )zero data retention(?! claim)/i, "a zero-data-retention claim"],
  [/\bZDR\b/, "a ZDR claim"],
  [/concurrency (of )?16/i, "the stale concurrency-16 claim"],
  [/\$\s?\d/, "a dollar amount beside CREDIT"],
  [/metadata only/i, "a metadata-only usage claim"],
];

function assertClean(text: string, where: string) {
  for (const [pattern, what] of BANNED) assert.doesNotMatch(text, pattern, `${where}: ${what}`);
}

test("P-01: the charge disclosure is the decided text, with the card's own rates", () => {
  assert.equal(PRICE.unit, "CREDIT");
  if (PRICE.unit !== "CREDIT") return;
  const d = chargeDisclosure(PRICE);
  assert.equal(
    d.intro[0],
    "Charges are in CREDIT, a product unit with no cash value. CREDIT has no USD exchange rate. A request is charged 400 CREDIT per million input tokens (video frames and text after preprocessing) and 1,200 CREDIT per million output tokens, rounded half-up to 8 decimal places. We place a hold for the maximum a request can cost and release the unused part.",
  );
  assert.equal(
    d.intro[1],
    "A request that completes is charged for its tokens. If you cancel a request or disconnect, it is charged only for tokens the model reports as consumed. If no usage is reported, nothing is charged.",
  );
  assert.deepEqual(d.neverCharged, [
    "requests we reject or refuse;",
    "requests that never ran;",
    "requests that time out on our side;",
    "requests that fail for platform reasons.",
  ]);
  assert.equal(d.outro, "Usage we cannot determine is released after 24 hours and never debited later.");
  const changed = chargeDisclosure({ ...PRICE, input: "450.50000000" as Credit, output: "1300.00000000" as Credit });
  assert.match(changed.intro[0], /charged 450\.5 CREDIT per million input tokens .* 1,300 CREDIT per million output/,
    "the rates come from the card, not from the copy");
});

test("P-26: the revocation copy is the decided text", () => {
  assert.equal(
    REVOCATION_COPY,
    "Revoking a key stops new requests immediately. Reads and cancels by that key stop within 60 seconds while our account service is reachable. During an account-service outage, a revoked key may continue to read or cancel its own existing jobs until the service recovers. It can never start new work.",
  );
});

test("the video contract is the record's: 82 s, one clip, 64 MiB, no live stream", () => {
  const facts = videoFacts(MODEL.capability).join("\n");
  assert.match(facts, /up to 82 seconds and 64 MiB decoded/);
  assert.match(facts, /video\/mp4, video\/quicktime, video\/webm/);
  assert.match(facts, /2 per second, 4 to 240 frames/);
  assert.match(facts, /Live video streams are not supported/);
  const longer = structuredClone(CREDIT_DOC);
  longer.capability.video.max_seconds = 60;
  assert.match(videoFacts(parsePublishedModel(longer).capability).join("\n"), /up to 60 seconds/, "read from the record");
});

test("retention says what serving stores and for how long, and that trace-off is not deletion", () => {
  const facts = retentionFacts(MODEL.retention).join("\n");
  assert.match(facts, /the request body you sent, the source video, the prepared \(sampled\) video frames, the model's answer, the streamed output events/);
  assert.match(facts, /This is not zero data retention/);
  assert.match(facts, /readable for 24 hours .* 410 result_expired/);
  assert.match(facts, /replayed from Last-Event-ID for 1 hour/);
  assert.match(facts, /processing cache for up to 7 days/);
  assert.match(facts, /does not delete the serving data/);
  assert.doesNotMatch(facts, /physically deleted/, "no deletion bound is claimed that the record does not state");
  const shorter = structuredClone(CREDIT_DOC);
  Object.assign(shorter.retention, { result_ttl_s: 3600, stream_journal_ttl_s: 600, idempotency_ttl_s: 7200, processing_cache_ttl_s: 86400, physical_deletion_bound_s: 172800 });
  const changed = retentionFacts(parsePublishedModel(shorter).retention).join("\n");
  assert.match(changed, /readable for 1 hour .* Last-Event-ID for 10 minutes/s, "the lifetimes are the record's");
  assert.match(changed, /for 2 hours after the job finishes[\s\S]*up to 24 hours[\s\S]*deleted within 2 days/);
});

test("durations and sizes read as people say them", () => {
  assert.deepEqual([86400, 604800, 3600, 90, 120, 172800].map(duration), ["24 hours", "7 days", "1 hour", "90 seconds", "2 minutes", "2 days"]);
  assert.deepEqual([67108864, 100663296, 1000].map(bytes), ["64 MiB", "96 MiB", "1,000 bytes"]);
  assert.equal(plainAmount("1200.00000000" as Credit), "1,200");
});

test("every error row is the gateway's code at the gateway's HTTP status", () => {
  for (const [code, status] of ERROR_ROWS) {
    assert.ok(ENVELOPES[code], `${code} is not a gateway error code`);
    assert.equal(status, ENVELOPES[code].http_status, code);
  }
  for (const needed of ["invalid_api_key", "insufficient_credit", "result_expired", "idempotency_conflict", "capacity_exhausted", "dependency_unavailable"]) {
    assert.ok(ERROR_ROWS.some(([code]) => code === needed), `the docs omit ${needed}`);
  }
});

test("every documented route is in the gateway's route table", () => {
  const routes = new Set([...ENDPOINT_DOC.matchAll(/^\| (GET|POST|PUT|DELETE) \| `([^`]+)` \|/gm)].map((m) => `${m[1]} ${m[2]}`));
  for (const [method, path] of ROUTE_ROWS) assert.ok(routes.has(`${method} ${path}`), `${method} ${path}`);
});

test("no rendered sentence carries a stale or false claim", () => {
  for (const sentence of allCopy(MODEL, PRICE)) assertClean(sentence, sentence.slice(0, 40));
});

test("the pages read only the published catalog: no public.models table, no USD columns, no stale copy", () => {
  for (const { path, source } of PAGES) {
    assert.doesNotMatch(source, /from\("models"\)/, `${path} reads public.models`);
    assert.doesNotMatch(source, /_usd_per_m|money\(|input_usd|output_usd/, `${path} shows USD columns`);
    assert.doesNotMatch(source, /marlin2b\.callbill\.ai|nemostation\/marlin/, `${path} hard-codes a model or origin`);
    assert.match(source, /loadCatalog\(apiBaseUrl\(process\.env\)\)/, `${path} does not read the published catalog`);
    assertClean(source, path);
  }
});

test("an unavailable catalog renders its fixed copy and no figure", () => {
  for (const { path, source } of PAGES) {
    assert.match(source, /catalog\.status !== "ok"[\s\S]{0,400}CATALOG_UNAVAILABLE/, `${path} has no unavailable branch`);
  }
});
