// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// F2C.c, console half: the published-model projection read from the Python package's own
// fixture directory, so the two halves cannot drift. Each test names its failure oracle.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  ContractRefusal,
  canonicalJson,
  parsePublishedModel,
  parseServingProfile,
  priceRequest,
  profileViolations,
  resolveModel,
  splitModel,
  type PublishedModel,
} from "../../../lib/contracts/v2/published-model.ts";

const DIR = new URL("../../../../infrx-api/infrx/contracts/v2/published/", import.meta.url);
const text = (name: string) => readFileSync(new URL(name, DIR), "utf8");
const load = (name: string) => JSON.parse(text(name));

type Patch = [string[], unknown];
type Case = { name: string; patches: Patch[]; expected?: string[] };

const CREDIT = load("published_marlin_credit.json");
const LEGACY = load("published_marlin_legacy_usd.json");
const PROFILE = parseServingProfile(load("serving_profile_marlin.json"));
const CASES = load("cases.json") as { refusals: Case[]; violations: Case[] };
const ALIASES = load("alias_compatibility.json") as {
  cases: { requested: string; credit: Record<string, unknown>; legacy_usd: Record<string, unknown> }[];
};

/** `published_fixtures.apply_patches`: set each path; a null value deletes the key. */
function applyPatches(doc: unknown, patches: Patch[]): unknown {
  const copy = JSON.parse(JSON.stringify(doc));
  for (const [path, value] of patches) {
    let node = copy;
    for (const key of path.slice(0, -1)) node = node[key];
    const last = path[path.length - 1];
    if (value === null) delete node[last];
    else node[last] = value;
  }
  return copy;
}

const paths = (violations: string[]) => violations.map((v) => v.split(":")[0]);

test("each record fixture re-serializes to the Python bytes exactly", () => {
  // Oracle: a field one half spells, orders or omits differently.
  for (const name of ["published_marlin_credit.json", "published_marlin_legacy_usd.json"]) {
    assert.equal(canonicalJson(parsePublishedModel(load(name))), text(name), name);
  }
  assert.equal(canonicalJson(PROFILE), text("serving_profile_marlin.json"));
});

for (const c of CASES.refusals) {
  test(`refused, as in Python: ${c.name}`, () => {
    // Oracles: ZDR claims, unknown video/tool features, USD parsed as CREDIT, coerced values,
    // unpriced regimes, spellings as USD keys, missing fields.
    assert.throws(() => parsePublishedModel(applyPatches(CREDIT, c.patches)), TypeError);
  });
}

for (const c of CASES.violations) {
  test(`profile check, as in Python: ${c.name}`, () => {
    // Oracle: a projection advertising what the deployed profile refuses, or a false alarm.
    const record = parsePublishedModel(applyPatches(CREDIT, c.patches));
    assert.deepEqual(paths(profileViolations(record, PROFILE)), c.expected);
  });
}

test("the Marlin fixtures are honest against the deployed profile", () => {
  for (const doc of [CREDIT, LEGACY]) assert.deepEqual(profileViolations(parsePublishedModel(doc), PROFILE), []);
  assert.equal(PROFILE.capability.video?.max_seconds, 82, "the deployed cap, not the 120 s default");
  assert.equal(PROFILE.retention.zero_data_retention, false);
});

for (const c of ALIASES.cases) {
  test(`alias ${c.requested} resolves and prices as in Python`, () => {
    // Oracle: the console resolving or pricing a spelling differently from admission (P-22).
    for (const [doc, expected] of [
      [CREDIT, c.credit],
      [LEGACY, c.legacy_usd],
    ] as const) {
      const published = [parsePublishedModel(doc)];
      if ("refused" in expected) {
        assert.throws(
          () => priceRequest(c.requested, published),
          (e: unknown) => e instanceof ContractRefusal && e.code === expected.refused,
        );
      } else {
        assert.equal(canonicalJson(priceRequest(c.requested, published)), canonicalJson(expected));
      }
    }
  });
}

test("pricing keeps the requested spelling and names exactly one identity", () => {
  // Oracle: the requested string replaced by the canonical one; a CREDIT card beside USD.
  const credit = priceRequest("nemostation/marlin-2b", [parsePublishedModel(CREDIT)]);
  assert.equal(credit.requested_model, "nemostation/marlin-2b");
  assert.equal(credit.model_revision, "nemostation/marlin-2b@2026-09-01");
  assert.equal(credit.price_version, undefined);
  const legacy = priceRequest("nemostation/marlin-2b", [parsePublishedModel(LEGACY)]);
  assert.equal(legacy.rate_card_version, undefined);
  assert.equal(legacy.price_version, "pv_marlin2b_usd_2026_09_r1");
});

test("a malformed name is not_found, like an unknown one", () => {
  for (const bad of ["", "@2026-09-01", "a@b@c", "nemostation/marlin 2b", "x".repeat(201), null]) {
    assert.throws(
      () => splitModel(bad),
      (e: unknown) => e instanceof ContractRefusal && e.code === "not_found",
    );
  }
  assert.deepEqual(splitModel("nemostation/marlin-2b"), ["nemostation/marlin-2b", null]);
});

test("the unlabelled id takes the highest listing; a pin keeps its revision", () => {
  // Oracle: an alias floating to an older listing, or a pin that floats.
  const old = parsePublishedModel(CREDIT);
  const newer: PublishedModel = {
    ...old,
    listing_version: 2,
    model_revision: "nemostation/marlin-2b@2026-10-01",
    serving: { ...old.serving, revision_label: "2026-10-01" },
  };
  assert.equal(resolveModel("nemostation/marlin-2b", [old, newer]), newer);
  assert.equal(resolveModel("nemostation/marlin-2b@2026-09-01", [old, newer]), old);
});
