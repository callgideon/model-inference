// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import money from "../../lib/contracts/fixtures/money.json" with { type: "json" };
import moneyCasesJson from "./money_cases.json" with { type: "json" };
import {
  addMoney,
  compareMoney,
  displayMoney,
  isMoney,
  isNegativeMoney,
  isZeroMoney,
  moneyFromUnits,
  moneyUnits,
  parseMoney,
  subMoney,
  sumMoney,
  tryMoneyFromUnits,
  tryParseMoneyUnits,
  ZERO_MONEY,
  type Money,
} from "../../lib/contracts/money.ts";

/**
 * The money domain is one contract across both languages (R11): `money_cases.json` is the
 * shared accept/reject list, data only and sorted by input, and the Python half asserts the
 * identical file. A disagreement is a defect in whichever side deviates, not a local tweak.
 */
const moneyCases = moneyCasesJson as unknown as {
  input: string;
  valid: boolean;
  canonical: string | null;
}[];

const fixture = money as unknown as {
  arithmetic: { a: string; b: string; sum: string; difference: string; compare: number }[];
  debits: {
    case: string;
    prompt_tokens: number;
    completion_tokens: number;
    charge_half_up: string;
    hold_ceiling: string;
    diverges?: boolean;
  }[];
  ledger_deltas: string[];
  display: { money: string; display: string }[];
};

test("the shared case list is the accept/reject domain, and both halves must agree on it", () => {
  const inputs = moneyCases.map((row) => row.input);
  assert.deepEqual(inputs, [...inputs].sort(), "money_cases.json must stay sorted by input");
  assert.equal(new Set(inputs).size, inputs.length, "money_cases.json must not repeat an input");

  for (const row of moneyCases) {
    const label = JSON.stringify(row.input);
    if (row.valid) {
      assert.ok(row.canonical !== null, `${label}: a valid case needs its canonical form`);
      assert.equal(parseMoney(row.input), row.canonical, label);
      assert.ok(isMoney(row.canonical), `${label}: ${row.canonical} must itself be canonical`);
      assert.equal(parseMoney(row.canonical), row.canonical, `${label}: canonical form is a fixed point`);
    } else {
      assert.equal(row.canonical, null, `${label}: an invalid case has no canonical form`);
      assert.equal(tryParseMoneyUnits(row.input), null, `${label} must not parse`);
      assert.equal(isMoney(row.input), false, `${label} must not look like money`);
      assert.throws(() => parseMoney(row.input), TypeError, `${label} must throw`);
    }
  }

  // The boundary and the cases that took a differential run to find are all present.
  const byInput = new Map(moneyCases.map((row) => [row.input, row]));
  for (const [input, valid] of [
    ["999999999999.99999999", true],
    ["1000000000000", false],
    ["0.000000001", false],
    ["1e3", false],
    ["NaN", false],
    ["-0", false],
    ["+1.00000000", false],
    ["1.00\n", false],
    ["", false],
    ["007.5", false],
    [".5", false],
    ["5.", false],
  ] as [string, boolean][]) {
    const row = byInput.get(input);
    assert.ok(row !== undefined, `money_cases.json must cover ${JSON.stringify(input)}`);
    assert.equal(row.valid, valid, `${JSON.stringify(input)} validity`);
  }
  assert.equal(ZERO_MONEY, "0.00000000");
  assert.equal(parseMoney("0"), ZERO_MONEY);
});

test("no number ever becomes money, however innocent it looks", () => {
  for (const bad of [0, 1, 0.1, 1e-8, NaN, Infinity, -0, null, undefined, {}, [], BigInt(10)]) {
    assert.equal(tryParseMoneyUnits(bad), null, `${String(bad)} must not parse`);
  }
  // A float can never sneak in through a helper either: the money module has no float path.
  const source = readFileSync(new URL("../../lib/contracts/money.ts", import.meta.url), "utf8");
  for (const forbidden of ["parseFloat", "toFixed", "Number(", "parseInt", "Math."]) {
    assert.ok(!source.includes(forbidden), `money.ts must not use ${forbidden}`);
  }
});

test("a value that is canonical but not eight digits is not money", () => {
  assert.equal(isMoney("1.5"), false);
  assert.equal(isMoney("1"), false);
  assert.equal(isMoney("1.50000000"), true);
  assert.equal(isMoney("-1.50000000"), true);
});

test("addition, subtraction, comparison and sums are exact at the eighth digit", () => {
  for (const row of fixture.arithmetic) {
    const a = parseMoney(row.a);
    const b = parseMoney(row.b);
    assert.equal(addMoney(a, b), row.sum, `${row.a} + ${row.b}`);
    assert.equal(subMoney(a, b), row.difference, `${row.a} - ${row.b}`);
    assert.equal(compareMoney(a, b), row.compare, `${row.a} <=> ${row.b}`);
    assert.equal(compareMoney(b, a), row.compare === 0 ? 0 : -row.compare, `${row.b} <=> ${row.a}`);
    assert.equal(subMoney(addMoney(a, b), b), a, "add then subtract round-trips");
  }
  const deltas = fixture.ledger_deltas.map(parseMoney);
  assert.equal(sumMoney(deltas), "29.99542080");
  assert.equal(sumMoney([]), ZERO_MONEY);
  // Float arithmetic would give 0.30000000000000004 here.
  assert.equal(addMoney(parseMoney("0.1"), parseMoney("0.2")), "0.30000000");
});

test("negative and zero are distinguishable, and negative zero does not exist", () => {
  assert.equal(isNegativeMoney(parseMoney("-0.00000001")), true);
  assert.equal(isNegativeMoney(ZERO_MONEY), false);
  assert.equal(isZeroMoney(ZERO_MONEY), true);
  assert.equal(isZeroMoney(parseMoney("-0.00000001")), false);
  assert.equal(subMoney(parseMoney("1.00000000"), parseMoney("1.00000000")), ZERO_MONEY);
  assert.equal(tryParseMoneyUnits("-0.00000000"), null);
  assert.equal(moneyFromUnits(BigInt(0)), ZERO_MONEY, "there is one zero, and it is unsigned");
});

test("scaled units are 1e-8 USD and stop at the twentieth significant digit", () => {
  assert.equal(moneyUnits(parseMoney("1")), BigInt(100000000));
  assert.equal(moneyUnits(parseMoney("-0.00000001")), BigInt(-1));
  // numeric(20, 8): 12 integral + 8 fractional digits, the same bound the Python half enforces.
  const largest = parseMoney("999999999999.99999999");
  assert.equal(moneyUnits(largest).toString(), "99999999999999999999");
  assert.throws(() => parseMoney("1000000000000"), TypeError, "13 integral digits do not fit");
  assert.throws(() => parseMoney("-1000000000000.00000000"), TypeError, "nor do they when negative");
  assert.throws(() => addMoney(largest, parseMoney("0.00000001")), RangeError, "a sum may not overflow");
  assert.throws(() => moneyUnits("1.5e0" as Money), TypeError, "a forged brand still fails at the boundary");
});

test("a prospective total can be tested for the domain without throwing", () => {
  // What a service needs before it mutates: an answer, not an exception.
  assert.equal(tryMoneyFromUnits(BigInt("99999999999999999999")), "999999999999.99999999");
  assert.equal(tryMoneyFromUnits(BigInt("100000000000000000000")), null, "one unit past the domain");
  assert.equal(tryMoneyFromUnits(BigInt("-100000000000000000000")), null, "and past it downwards");
  assert.equal(tryMoneyFromUnits(BigInt(0)), ZERO_MONEY);
  assert.equal(
    tryMoneyFromUnits(moneyUnits(parseMoney("999999999999.99999999")) + BigInt(1)),
    null,
    "the sum a grant would produce is testable in advance",
  );
});

test("leading zeros are rejected, as they are on the Python side", () => {
  for (const bad of ["007.5", "01.5", "-01.5", "00", "00.00000000"]) {
    assert.equal(tryParseMoneyUnits(bad), null, `${bad} must not parse`);
  }
  assert.equal(parseMoney("0.50000000"), "0.50000000", "a single leading zero is the canonical form");
});

test("the charge and hold fixtures agree across languages and stay canonical", () => {
  let diverged = 0;
  for (const debit of fixture.debits) {
    assert.ok(isMoney(debit.charge_half_up), `${debit.case}: charge not canonical`);
    assert.ok(isMoney(debit.hold_ceiling), `${debit.case}: hold not canonical`);
    // The hold rounds up, so it can never be below the charge it must cover.
    assert.ok(
      compareMoney(parseMoney(debit.hold_ceiling), parseMoney(debit.charge_half_up)) >= 0,
      `${debit.case}: a hold must cover its charge`,
    );
    if (debit.diverges === true) {
      diverged += 1;
      assert.notEqual(debit.charge_half_up, debit.hold_ceiling, `${debit.case}: divergence expected`);
    }
    if (debit.prompt_tokens === 0 && debit.completion_tokens === 0) {
      assert.equal(debit.charge_half_up, ZERO_MONEY, "a zero-token request costs nothing");
    }
  }
  assert.equal(diverged, 1, "the ceiling-vs-half-up case must be present");
});

test("display never routes the value through Number", () => {
  for (const row of fixture.display) {
    assert.equal(displayMoney(parseMoney(row.money)), row.display, row.money);
  }
  // The eighth digit survives; Number(0.000000015) would not.
  assert.equal(displayMoney(parseMoney("0.00000015")), "$0.00000015");
  assert.equal(displayMoney(parseMoney("1.00000000"), 8), "$1.00000000");
  assert.equal(displayMoney(parseMoney("1.00000000"), 0), "$1");
});
