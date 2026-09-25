// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// CREDIT-UNITS, console half (F2P item 2). Two kinds of check live here:
//
//   * runtime: a declared unit is taken from the payload and a wrong one is refused,
//     because the brand does not exist at runtime;
//   * compile time: the `@ts-expect-error` blocks below are the proof that mixed-unit
//     arithmetic does not type-check. `pnpm exec tsc --noEmit` covers this file, so
//     removing a brand turns each `@ts-expect-error` into "unused directive" and the
//     typecheck fails. That is the TS half of "refused by construction".
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  ACCOUNTING_REGIMES,
  MONEY_UNITS,
  REGIME_UNIT,
  SURFACE_VERSION,
  V2_SCHEMA_VERSION,
  ZERO_CREDIT,
  addCredit,
  addProviderUsd,
  addUsd,
  compareCredit,
  displayCredit,
  isCanonicalAmount,
  isNegativeCredit,
  parseAmount,
  parseCredit,
  parseProviderUsd,
  parseUsd,
  subCredit,
  totalCredit,
  totalUsd,
  unitOfRegime,
  type Credit,
  type ProviderUsd,
  type Usd,
} from "../../../lib/contracts/v2/money-units.ts";

/** The Python half's generated table; read from its directory so no copy can drift. */
const CASES = JSON.parse(
  readFileSync(
    new URL(
      "../../../../infrx-api/infrx/contracts/fixtures/v2/money_unit_cases.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as { unit: string; input: string; valid: boolean; canonical: string | null }[];

test("the generated money-unit table is classified exactly as Python classifies it", () => {
  assert.ok(CASES.length >= 15, `only ${CASES.length} parity cases`);
  for (const { unit, input, valid, canonical } of CASES) {
    let got: string | null = null;
    try {
      got = parseAmount(input, unit) as string;
    } catch {
      got = null;
    }
    assert.equal(got, valid ? canonical : null, `${unit} ${JSON.stringify(input)}`);
  }
});

test("a wrong or unknown unit token is refused, since the brand is gone at runtime", () => {
  assert.throws(() => parseAmount("1.00000000", "CREDITS"), /unknown money unit/);
  assert.throws(() => parseAmount("1.00000000", ""), /unknown money unit/);
  assert.throws(() => parseAmount("1.00000000", undefined), /unknown money unit/);
  assert.deepEqual([...MONEY_UNITS], ["CREDIT", "USD", "PROVIDER_USD"]);
});

test("the accounting regime fixes the unit and an unknown regime refuses", () => {
  assert.equal(unitOfRegime("credit"), "CREDIT");
  assert.equal(unitOfRegime("legacy_usd"), "USD");
  assert.deepEqual([...ACCOUNTING_REGIMES], ["legacy_usd", "credit"]);
  assert.deepEqual(REGIME_UNIT, { legacy_usd: "USD", credit: "CREDIT" });
  for (const bad of ["CREDIT", "usd", "", null, 1]) {
    assert.throws(() => unitOfRegime(bad), /unknown accounting regime/);
  }
});

test("same-unit arithmetic is exact at eight places and overflow throws", () => {
  const grant = parseCredit("10000.00000000");
  assert.equal(addCredit(grant, parseCredit("0.00000001")), "10000.00000001");
  assert.equal(subCredit(grant, parseCredit("0.00000001")), "9999.99999999");
  assert.equal(totalCredit([]), ZERO_CREDIT);
  assert.equal(totalCredit([parseCredit("1.5"), parseCredit("2.25")]), "3.75000000");
  assert.equal(totalUsd([parseUsd("0.01414000"), parseUsd("4.20086")]), "4.21500000");
  assert.equal(compareCredit(parseCredit("1"), parseCredit("2")), -1);
  assert.equal(compareCredit(parseCredit("2"), parseCredit("2")), 0);
  assert.equal(isNegativeCredit(parseCredit("-0.00000001")), true);
  assert.throws(
    () => addCredit(parseCredit("999999999999.99999999"), parseCredit("0.00000001")),
    /overflows/,
  );
});

test("mixed-unit arithmetic does not compile", () => {
  // Deliberately never invoked: the assertion IS the typecheck. If the brands stop
  // disagreeing, each `@ts-expect-error` becomes an unused directive and
  // `pnpm exec tsc --noEmit` fails — which is the only honest way to check a
  // compile-time guarantee from a runtime test.
  function wouldNotCompile() {
    const credit = parseCredit("1.00000000");
    const usd = parseUsd("1.00000000");
    const provider = parseProviderUsd("1.00000000");
    // @ts-expect-error a Usd is not a Credit
    addCredit(credit, usd);
    // @ts-expect-error a ProviderUsd is not a Credit
    addCredit(credit, provider);
    // @ts-expect-error a Credit is not a Usd
    addUsd(usd, credit);
    // @ts-expect-error a Usd is not a ProviderUsd — external budgets are their own unit
    addProviderUsd(provider, usd);
    // @ts-expect-error an unbranded string is not a Credit
    addCredit(credit, "1.00000000");
    // @ts-expect-error a number is never a monetary value
    addCredit(credit, 1);
  }
  assert.equal(typeof wouldNotCompile, "function");
});

test("no conversion function is exported, and none can be improvised", async () => {
  const exported = (await import("../../../lib/contracts/v2/money-units.ts")) as Record<
    string,
    unknown
  >;
  const forbidden = ["convert", "exchange", "tousd", "tocredit", "inusd", "incredit", "rateto"];
  const offenders = Object.keys(exported).filter((name) =>
    forbidden.some((bad) => name.toLowerCase().includes(bad)),
  );
  assert.deepEqual(offenders, [], `conversion-shaped exports: ${offenders.join(", ")}`);
  // The type-level half: assigning across brands is an error, so there is no
  // "cast it and move on" path either.
  const credit: Credit = parseCredit("1.00000000");
  // @ts-expect-error a Credit cannot be read as a Usd
  const asUsd: Usd = credit;
  // @ts-expect-error nor as a ProviderUsd
  const asProvider: ProviderUsd = credit;
  assert.ok(asUsd && asProvider);
});

test("credits are displayed as credits, never with a currency symbol", () => {
  assert.equal(displayCredit(parseCredit("10000.00000000")), "10,000.00 credits");
  assert.equal(displayCredit(parseCredit("9.97600000")), "9.976 credits");
  assert.equal(displayCredit(parseCredit("-1.50000000")), "-1.50 credits");
  assert.ok(!displayCredit(parseCredit("1")).includes("$"));
});

test("the surface and schema version are declared once, and canonical form is exact", () => {
  // The cross-language comparison of these two lives in
  // tests/contracts/v2/test_parity_v2.py, which reads both halves' sources.
  assert.equal(SURFACE_VERSION, "contracts-v2.1");
  assert.equal(V2_SCHEMA_VERSION, 2);
  assert.equal(isCanonicalAmount("1.00000000"), true);
  assert.equal(isCanonicalAmount("1.0"), false);
  assert.equal(isCanonicalAmount(1), false);
});
