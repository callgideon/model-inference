/**
 * USD money for the console — contracts v1 §Money, encoding 08 §4.
 *
 * A `Money` value is a plain fixed-point decimal string with exactly eight
 * fractional digits ("0.10000000"). Arithmetic runs on BigInt units of 1e-8 USD;
 * `number` is never used for a monetary value, not even for display.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

export type Money = string & { readonly __money: "USD" };

/** Fractional digits of the wire form and of `numeric(20, 8)`. */
export const MONEY_SCALE = 8;

/** The project's tsconfig targets ES2017, where BigInt literals are unavailable; the call is not. */
const ZERO_UNITS = BigInt(0);

/**
 * Plain decimal only: no exponent, no sign but a leading `-`, no whitespace, no `NaN`/`Infinity`,
 * no leading zeros. Deliberately the same grammar as the Python half's `_PLAIN`
 * (`infrx/contracts/money.py`): both ends of a request must accept exactly the same strings.
 */
const PLAIN_DECIMAL = /^-?(?:0|[1-9][0-9]*)(\.[0-9]+)?$/;

/**
 * `numeric(20, 8)` holds 20 significant digits — 12 integral plus 8 fractional — so the scaled
 * unit magnitude is bounded by 1e20. A larger value is refused here rather than accepted into a
 * DTO and then rejected by PostgreSQL. Matches `MAX_DIGITS = 20` on the Python side.
 */
const MAX_UNITS = BigInt("100000000000000000000");

export const ZERO_MONEY = "0.00000000" as Money;

/**
 * Scaled units (1 = 1e-8 USD) for any accepted plain decimal string, or null.
 * Rejects non-strings (a float that got as far as JSON is a bug, not a value),
 * exponents, leading zeros, more precision than the scale can hold, negative zero,
 * and magnitudes `numeric(20, 8)` cannot store.
 */
export function tryParseMoneyUnits(value: unknown): bigint | null {
  if (typeof value !== "string" || !PLAIN_DECIMAL.test(value)) return null;
  const negative = value.startsWith("-");
  const digits = negative ? value.slice(1) : value;
  const dot = digits.indexOf(".");
  const whole = dot === -1 ? digits : digits.slice(0, dot);
  const fraction = dot === -1 ? "" : digits.slice(dot + 1);
  if (fraction.length > MONEY_SCALE) return null;
  const units = BigInt(whole + fraction.padEnd(MONEY_SCALE, "0"));
  if (negative && units === ZERO_UNITS) return null;
  if (units >= MAX_UNITS) return null;
  return negative ? -units : units;
}

/**
 * The canonical eight-digit string for scaled units, or null when the value is outside
 * `numeric(20, 8)`. Every caller that computes a *prospective* total — a grant about to be
 * appended, a balance about to be reported — uses this and decides what to do, because a
 * service must reject an out-of-domain amount before it mutates anything, not throw halfway.
 */
export function tryMoneyFromUnits(units: bigint): Money | null {
  const negative = units < ZERO_UNITS;
  const magnitude = negative ? -units : units;
  if (magnitude >= MAX_UNITS) return null;
  const digits = magnitude.toString().padStart(MONEY_SCALE + 1, "0");
  const whole = digits.slice(0, -MONEY_SCALE);
  return `${negative ? "-" : ""}${whole}.${digits.slice(-MONEY_SCALE)}` as Money;
}

/** The canonical eight-digit string for scaled units. Overflow throws; it is never truncated. */
export function moneyFromUnits(units: bigint): Money {
  const value = tryMoneyFromUnits(units);
  if (value === null) {
    const magnitude = units < ZERO_UNITS ? -units : units;
    throw new RangeError(`money overflows numeric(20, 8): ${magnitude.toString()} units`);
  }
  return value;
}

/** Parse and normalise, or throw. Use at every trust boundary that yields money. */
export function parseMoney(value: unknown): Money {
  const units = tryParseMoneyUnits(value);
  if (units === null) throw new TypeError(`not a USD decimal string: ${JSON.stringify(value)}`);
  return moneyFromUnits(units);
}

/** True only for the canonical form, so fixtures and DTOs cannot drift to "1.5". */
export function isMoney(value: unknown): value is Money {
  const units = tryParseMoneyUnits(value);
  return units !== null && moneyFromUnits(units) === value;
}

export function moneyUnits(value: Money): bigint {
  const units = tryParseMoneyUnits(value);
  if (units === null) throw new TypeError(`not a USD decimal string: ${JSON.stringify(value)}`);
  return units;
}

export function addMoney(a: Money, b: Money): Money {
  return moneyFromUnits(moneyUnits(a) + moneyUnits(b));
}

export function subMoney(a: Money, b: Money): Money {
  return moneyFromUnits(moneyUnits(a) - moneyUnits(b));
}

export function sumMoney(values: readonly Money[]): Money {
  let units = ZERO_UNITS;
  for (const value of values) units += moneyUnits(value);
  return moneyFromUnits(units);
}

export function compareMoney(a: Money, b: Money): -1 | 0 | 1 {
  const left = moneyUnits(a);
  const right = moneyUnits(b);
  return left < right ? -1 : left > right ? 1 : 0;
}

export function isNegativeMoney(value: Money): boolean {
  return moneyUnits(value) < ZERO_UNITS;
}

export function isZeroMoney(value: Money): boolean {
  return moneyUnits(value) === ZERO_UNITS;
}

function group(digits: string): string {
  return digits.replace(/\B(?=([0-9]{3})+(?![0-9]))/g, ",");
}

/**
 * Display form: "$1,234.56", widened for sub-cent amounts ("$0.0001234").
 * String and BigInt only — a `Number` round-trip would lose the eighth digit.
 */
export function displayMoney(value: Money, minFractionDigits = 2): string {
  const units = moneyUnits(value);
  const negative = units < ZERO_UNITS;
  const digits = (negative ? -units : units).toString().padStart(MONEY_SCALE + 1, "0");
  let fraction = digits.slice(-MONEY_SCALE);
  while (fraction.length > minFractionDigits && fraction.endsWith("0")) {
    fraction = fraction.slice(0, -1);
  }
  const shown = fraction.length > 0 ? `.${fraction}` : "";
  return `${negative ? "-" : ""}$${group(digits.slice(0, -MONEY_SCALE))}${shown}`;
}
