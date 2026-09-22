/**
 * Three denominations for the console — contracts v2, F2P item 2.
 *
 * The arithmetic is `../money.ts`'s: BigInt units of 1e-8, the same plain-decimal
 * grammar as `infrx/contracts/money.py`, `number` never touching a monetary value.
 * This module adds the **unit**, which v1 did not have.
 *
 * `Credit`, `Usd` and `ProviderUsd` are branded string types over that one
 * representation. The brands are disjoint, so `addCredit(credit, usd)` does not
 * compile — `tests/contracts/v2/money-units.test.ts` proves that with
 * `@ts-expect-error`, which `pnpm exec tsc --noEmit` checks — and every parser
 * takes the unit token from the payload's own explicit field, so a wrong-unit
 * value is refused at runtime too, where the brand no longer exists.
 *
 * **No conversion function exists**, here or in the Python half: the plan fixes no
 * exchange rate between CREDIT and USD (`research/platforms/02-credits.md`), and a
 * function producing one would be inventing product policy.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import { MONEY_SCALE, tryMoneyFromUnits, tryParseMoneyUnits } from "../money.ts";

/**
 * The one reviewed identifier for the whole changed consumer/provider surface
 * (F2P item 7). `infrx/contracts/v2/__init__.py` declares the same string and
 * `tests/contracts/v2/test_parity_v2.py` fails if the two drift.
 */
export const SURFACE_VERSION = "contracts-v2.0";

/** Internal v2 schemas carry this; public OpenAI-style bodies do not change. */
export const V2_SCHEMA_VERSION = 2;

export const MONEY_UNITS = ["CREDIT", "USD", "PROVIDER_USD"] as const;
export type MoneyUnit = (typeof MONEY_UNITS)[number];

/** A row's accounting regime (06-database-map.md `usage_events`). Fixes its unit. */
export const ACCOUNTING_REGIMES = ["legacy_usd", "credit"] as const;
export type AccountingRegime = (typeof ACCOUNTING_REGIMES)[number];

/** The only regime/unit pairing there is. A regime never implies a conversion. */
export const REGIME_UNIT: Readonly<Record<AccountingRegime, MoneyUnit>> = Object.freeze({
  legacy_usd: "USD",
  credit: "CREDIT",
});

/** The consumer product unit. Exact numeric(20, 8) CREDIT; never rendered with "$". */
export type Credit = string & { readonly __unit: "CREDIT" };

/** The legacy regime's dollars: existing delta_usd / cost_usd history, preserved. */
export type Usd = string & { readonly __unit: "USD" };

/** External provider/teacher/judge budgets. Dollars, but never a customer balance. */
export type ProviderUsd = string & { readonly __unit: "PROVIDER_USD" };

export type Amount = Credit | Usd | ProviderUsd;

export const ZERO_CREDIT = "0.00000000" as Credit;
export const ZERO_USD = "0.00000000" as Usd;
export const ZERO_PROVIDER_USD = "0.00000000" as ProviderUsd;

/** Is this a canonical eight-digit decimal in the shared domain? Unit-blind on purpose. */
export function isCanonicalAmount(value: unknown): value is string {
  const units = tryParseMoneyUnits(value);
  return units !== null && tryMoneyFromUnits(units) === value;
}

function parse<T extends Amount>(value: unknown, unit: MoneyUnit): T {
  const units = tryParseMoneyUnits(value);
  if (units === null) throw new TypeError(`not a ${unit} decimal string: ${JSON.stringify(value)}`);
  const canonical = tryMoneyFromUnits(units);
  if (canonical === null) throw new RangeError(`${unit} amount overflows numeric(20, 8)`);
  return canonical as unknown as T;
}

export function parseCredit(value: unknown): Credit {
  return parse<Credit>(value, "CREDIT");
}

export function parseUsd(value: unknown): Usd {
  return parse<Usd>(value, "USD");
}

export function parseProviderUsd(value: unknown): ProviderUsd {
  return parse<ProviderUsd>(value, "PROVIDER_USD");
}

const PARSERS = {
  CREDIT: parseCredit,
  USD: parseUsd,
  PROVIDER_USD: parseProviderUsd,
} as const;

/**
 * A stored or transmitted `{amount, unit}` pair, parsed under the unit the payload
 * itself declares. The runtime half of the brand: a DTO that says "USD" can never
 * be read as a `Credit` just because the reading code wanted one.
 */
export function parseAmount(value: unknown, unit: unknown): Amount {
  if (typeof unit !== "string" || !(MONEY_UNITS as readonly string[]).includes(unit)) {
    throw new TypeError(`unknown money unit: ${JSON.stringify(unit)}`);
  }
  return PARSERS[unit as MoneyUnit](value);
}

/** The unit an accounting regime may carry. An unknown regime refuses. */
export function unitOfRegime(regime: unknown): MoneyUnit {
  if (typeof regime !== "string" || !(ACCOUNTING_REGIMES as readonly string[]).includes(regime)) {
    throw new TypeError(`unknown accounting regime: ${JSON.stringify(regime)}`);
  }
  return REGIME_UNIT[regime as AccountingRegime];
}

/**
 * Same-unit arithmetic. The brand makes the type parameter collapse to one unit, so
 * `add(credit, usd)` is a compile error and no runtime tag is needed to catch it.
 */
function add<T extends Amount>(a: T, b: T, unit: MoneyUnit): T {
  const left = tryParseMoneyUnits(a);
  const right = tryParseMoneyUnits(b);
  if (left === null || right === null) throw new TypeError(`not a ${unit} decimal string`);
  const sum = tryMoneyFromUnits(left + right);
  if (sum === null) throw new RangeError(`${unit} total overflows numeric(20, 8)`);
  return sum as unknown as T;
}

export function addCredit(a: Credit, b: Credit): Credit {
  return add(a, b, "CREDIT");
}

export function subCredit(a: Credit, b: Credit): Credit {
  return add(a, negate(b, "CREDIT"), "CREDIT");
}

export function addUsd(a: Usd, b: Usd): Usd {
  return add(a, b, "USD");
}

export function addProviderUsd(a: ProviderUsd, b: ProviderUsd): ProviderUsd {
  return add(a, b, "PROVIDER_USD");
}

function negate<T extends Amount>(value: T, unit: MoneyUnit): T {
  const units = tryParseMoneyUnits(value);
  if (units === null) throw new TypeError(`not a ${unit} decimal string`);
  const negated = tryMoneyFromUnits(-units);
  if (negated === null) throw new RangeError(`${unit} amount overflows numeric(20, 8)`);
  return negated as unknown as T;
}

/** Sum of one unit's amounts; an empty history still answers in that unit, as zero. */
export function totalCredit(amounts: readonly Credit[]): Credit {
  return amounts.reduce(addCredit, ZERO_CREDIT);
}

export function totalUsd(amounts: readonly Usd[]): Usd {
  return amounts.reduce(addUsd, ZERO_USD);
}

export function compareCredit(a: Credit, b: Credit): -1 | 0 | 1 {
  const left = tryParseMoneyUnits(a);
  const right = tryParseMoneyUnits(b);
  if (left === null || right === null) throw new TypeError("not a CREDIT decimal string");
  return left < right ? -1 : left > right ? 1 : 0;
}

export function isNegativeCredit(value: Credit): boolean {
  return compareCredit(value, ZERO_CREDIT) < 0;
}

/**
 * Display form for credits: grouped digits and the unit word, never a currency
 * symbol (`02-credits.md`: "do not display a dollar symbol"). `displayMoney` in
 * `../money.ts` keeps printing "$" and stays for the legacy USD statement.
 */
export function displayCredit(value: Credit, minFractionDigits = 2): string {
  const units = tryParseMoneyUnits(value);
  if (units === null) throw new TypeError("not a CREDIT decimal string");
  const negative = units < BigInt(0);
  const digits = (negative ? -units : units).toString().padStart(MONEY_SCALE + 1, "0");
  let fraction = digits.slice(-MONEY_SCALE);
  while (fraction.length > minFractionDigits && fraction.endsWith("0")) {
    fraction = fraction.slice(0, -1);
  }
  const whole = digits.slice(0, -MONEY_SCALE).replace(/\B(?=([0-9]{3})+(?![0-9]))/g, ",");
  return `${negative ? "-" : ""}${whole}${fraction.length > 0 ? `.${fraction}` : ""} credits`;
}
