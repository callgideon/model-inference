// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
import assert from "node:assert/strict";
import test from "node:test";
import money from "../../lib/contracts/fixtures/money.json" with { type: "json" };
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
  tryParseMoneyUnits,
  ZERO_MONEY,
  type Money,
} from "../../lib/contracts/money.ts";

const fixture = money as unknown as {
  canonical: { input: string; money: string }[];
  invalid: string[];
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

test("parsing normalises to exactly eight fractional digits", () => {
  for (const { input, money: expected } of fixture.canonical) {
    assert.equal(parseMoney(input), expected, input);
    assert.ok(isMoney(expected), `${expected} should be canonical`);
    assert.equal(parseMoney(expected), expected, "canonical form is a fixed point");
  }
  assert.equal(ZERO_MONEY, "0.00000000");
  assert.equal(parseMoney("0"), ZERO_MONEY);
});

test("everything that is not a plain decimal string is rejected", () => {
  for (const bad of fixture.invalid) {
    assert.equal(tryParseMoneyUnits(bad), null, `${JSON.stringify(bad)} must not parse`);
    assert.equal(isMoney(bad), false, `${JSON.stringify(bad)} must not look like money`);
    assert.throws(() => parseMoney(bad), TypeError, `${JSON.stringify(bad)} must throw`);
  }
  // Numbers never reach the money type, however innocent they look.
  for (const bad of [0, 1, 0.1, 1e-8, NaN, Infinity, -0, null, undefined, {}, [], BigInt(10)]) {
    assert.equal(tryParseMoneyUnits(bad), null, `${String(bad)} must not parse`);
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

test("scaled units are 1e-8 USD and survive twenty significant digits", () => {
  assert.equal(moneyUnits(parseMoney("1")), BigInt(100000000));
  assert.equal(moneyUnits(parseMoney("-0.00000001")), BigInt(-1));
  const big = parseMoney("12345678901234567890");
  assert.equal(moneyUnits(big).toString(), "1234567890123456789000000000");
  assert.equal(addMoney(big, parseMoney("0.00000001")), "12345678901234567890.00000001");
  assert.throws(() => moneyUnits("1.5e0" as Money), TypeError, "a forged brand still fails at the boundary");
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
