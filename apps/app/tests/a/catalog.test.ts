// node --test "tests/**/*.test.ts"
//
// A3 (CREDIT-RATE, SPLIT-CONTRACT): the Models/Docs catalog is the gateway's published projection
// and nothing else. Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  aliasResolutions,
  apiBaseUrl,
  holdCredit,
  loadCatalog,
  priceView,
  type Catalog,
} from "../../app/(console)/models/catalog.ts";
import { parsePublishedModel, type PublishedModel } from "../../lib/contracts/v2/published-model.ts";

const DIR = new URL("../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const load = (name: string) => JSON.parse(readFileSync(new URL(name, DIR), "utf8"));
const CREDIT_DOC = load("published_marlin_credit.json");
const LEGACY_DOC = load("published_marlin_legacy_usd.json");
const CREDIT = parsePublishedModel(structuredClone(CREDIT_DOC));
const LEGACY = parsePublishedModel(structuredClone(LEGACY_DOC));
const ALIASES = load("alias_compatibility.json") as { cases: { requested: string; credit: Record<string, unknown> }[] };
const BASE = "https://api.example.test";

type Seen = { url: string; init?: RequestInit };
function replying(status: number, body: unknown, seen: Seen[] = []) {
  return async (url: string, init?: RequestInit) => {
    seen.push({ url, init });
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
  };
}
const list = (...data: unknown[]) => ({ object: "list", data });

function unavailable(catalog: Catalog, reason: string) {
  assert.deepEqual(catalog, { status: "unavailable", reason }, "an unavailable read carries no models at all");
}

test("the catalog is the published list at <origin>/v1/models, asked without any credential", async () => {
  const seen: Seen[] = [];
  const catalog = await loadCatalog(BASE, replying(200, list(CREDIT_DOC), seen));
  assert.equal(catalog.status, "ok");
  assert.deepEqual(catalog.status === "ok" && catalog.models, [CREDIT]);
  assert.equal(seen[0].url, `${BASE}/v1/models`);
  const headers = new Headers(seen[0].init?.headers);
  assert.equal(headers.get("authorization"), null, "discovery is public: no key leaves the server");
  assert.equal(seen[0].init?.cache, "no-store", "availability is live, never a cached claim");
});

test("no configured origin is unavailable, and nothing is fetched", async () => {
  const seen: Seen[] = [];
  unavailable(await loadCatalog(null, replying(200, list(CREDIT_DOC), seen)), "not_configured");
  assert.equal(seen.length, 0);
});

test("a gateway that cannot answer is unavailable, never a fixture or an empty catalog", async () => {
  unavailable(await loadCatalog(BASE, async () => { throw new TypeError("fetch failed"); }), "unreachable");
  unavailable(await loadCatalog(BASE, replying(503, { error: { code: "dependency_unavailable" } })), "unreachable");
  unavailable(await loadCatalog(BASE, replying(200, "<html>")), "malformed");
  unavailable(await loadCatalog(BASE, replying(200, { data: [CREDIT_DOC] })), "malformed");
  unavailable(await loadCatalog(BASE, replying(200, list(CREDIT_DOC, "nope"))), "malformed");
});

test("one record the contract refuses fails the whole read: a false ZDR claim is never shown", async () => {
  const zdr = structuredClone(CREDIT_DOC);
  zdr.retention.zero_data_retention = true;
  unavailable(await loadCatalog(BASE, replying(200, list(CREDIT_DOC, zdr))), "malformed");
  const tools = structuredClone(CREDIT_DOC);
  tools.capability.tools = true;
  unavailable(await loadCatalog(BASE, replying(200, list(tools))), "malformed");
  const usdAsCredit = structuredClone(CREDIT_DOC);
  usdAsCredit.pricing.credit.unit = "USD";
  unavailable(await loadCatalog(BASE, replying(200, list(usdAsCredit))), "malformed");
});

test("an empty published list is ok and empty: the page says nothing is published", async () => {
  assert.deepEqual(await loadCatalog(BASE, replying(200, list())), { status: "ok", baseUrl: BASE, models: [] });
});

test("the API origin is https (loopback http for development), with no path, query or credentials", () => {
  const at = (v?: string) => apiBaseUrl({ INFRX_API_BASE_URL: v });
  assert.equal(at("https://api.example.test"), "https://api.example.test");
  assert.equal(at(" https://api.example.test/ "), "https://api.example.test");
  assert.equal(at("http://127.0.0.1:8001"), "http://127.0.0.1:8001");
  for (const bad of [undefined, "", "not a url", "http://api.example.test", "https://api.example.test/v1", "https://u:p@api.example.test", "https://api.example.test/?x=1", "ftp://api.example.test"]) {
    assert.equal(at(bad), null, String(bad));
  }
});

test("CREDIT-RATE: the credit regime shows the approved card in CREDIT only, never a USD figure", () => {
  const price = priceView(CREDIT);
  assert.deepEqual(
    { ...price },
    {
      unit: "CREDIT",
      version: CREDIT_DOC.pricing.credit.rate_card_version,
      input: "400.00000000",
      output: "1200.00000000",
      provisional: CREDIT_DOC.pricing.credit.provisional,
      effectiveAt: CREDIT_DOC.pricing.credit.effective_at,
      maxHold: "14.74560000",
    },
  );
  assert.ok(!JSON.stringify(price).includes(CREDIT_DOC.pricing.legacy_usd.input_rate_per_million), "no USD number beside CREDIT");
});

test("SPLIT-CONTRACT: the legacy regime shows its USD price identity only, never a converted CREDIT", () => {
  const price = priceView(LEGACY);
  assert.deepEqual({ ...price }, {
    unit: "USD",
    version: "pv_marlin2b_usd_2026_09_r1",
    input: "0.10000000",
    output: "0.30000000",
  });
  assert.throws(() => holdCredit(LEGACY, null), /no CREDIT card/);
});

test("a provisional card is reported as provisional, so no page can present it as approved", () => {
  const approved = structuredClone(CREDIT_DOC);
  approved.pricing.credit.provisional = false;
  assert.equal(priceView(parsePublishedModel(approved)).unit === "CREDIT" && (priceView(parsePublishedModel(approved)) as { provisional: boolean }).provisional, false);
  assert.equal((priceView(CREDIT) as { provisional: boolean }).provisional, true);
});

test("the hold is P-01's arithmetic: 14.7456 at the caps, less with a smaller max_tokens, rounded up", () => {
  assert.equal(holdCredit(CREDIT, null), "14.74560000"); // 30720 x 400 + 2048 x 1200, per million (P-01)
  assert.equal(holdCredit(CREDIT, 512), "12.90240000"); // the input ceiling stays 30720; the output side shrinks
  const tiny = structuredClone(CREDIT_DOC);
  tiny.pricing.credit.input_rate_per_million = "0.00000001";
  tiny.pricing.credit.output_rate_per_million = "0.00000000";
  assert.equal(holdCredit(parsePublishedModel(tiny), 1), "0.00000001", "a fraction of a unit rounds up, never down");
});

test("every alias the record lists resolves to the same revision, deployment and card (P-22)", () => {
  const resolutions = aliasResolutions(CREDIT, [CREDIT]);
  assert.deepEqual(resolutions.map((r) => r.requested_model), CREDIT.aliases);
  const pins = new Set(resolutions.map((r) => `${r.model_revision}|${r.deployment_revision_id}|${r.rate_card_version}`));
  assert.equal(pins.size, 1, [...pins].join(" / "));
  for (const expected of ALIASES.cases) {
    if (!CREDIT.aliases.includes(expected.requested)) continue;
    assert.deepEqual({ ...resolutions.find((r) => r.requested_model === expected.requested) }, expected.credit);
  }
  const legacy = aliasResolutions(LEGACY, [LEGACY]);
  assert.ok(legacy.every((r) => r.price_version === "pv_marlin2b_usd_2026_09_r1" && r.rate_card_version === undefined));
});

test("a later listing of the same model wins resolution, so the page never shows a superseded card", () => {
  const later = structuredClone(CREDIT_DOC);
  later.listing_version = 2;
  later.pricing.credit.rate_card_version = "rc_marlin2b_20260925_launch";
  const models: PublishedModel[] = [CREDIT, parsePublishedModel(later)];
  assert.ok(aliasResolutions(CREDIT, models).every((r) => r.rate_card_version === "rc_marlin2b_20260925_launch"));
});
