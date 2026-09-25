// node --test "tests/**/*.test.ts"
//
// U1R shared money display helpers (`lib/format.ts`). CREDIT and legacy USD are exact decimal
// strings with their own labels: nothing here goes through `Number`, and nothing prints one unit
// with the other's label (02-credits: "do not display a dollar symbol" for credits).
import assert from "node:assert/strict";
import test from "node:test";

import { amount, credits, signedAmount, usd } from "../../lib/format.ts";

const T = {
  exact: "U1R-F01 credits and legacy USD keep every one of the eight digits, with no float round-trip",
  label: "U1R-F02 a CREDIT amount never carries a dollar sign and a USD amount is always labelled USD",
  refuse: "U1R-F03 a number, a malformed string or an unknown unit is refused, never displayed",
  signed: "U1R-F04 a signed ledger amount reads as a credit or a debit in its own unit",
};

test(T.exact, () => {
  assert.equal(credits("10000.00000000"), "10,000.00 credits");
  assert.equal(credits("0.00000001"), "0.00000001 credits");
  // 2^53 + 1 units of 1e-8 is where a double stops being exact.
  assert.equal(credits("90071992.54740993"), "90,071,992.54740993 credits");
  assert.equal(usd("0.00019660"), "$0.0001966 USD");
  assert.equal(usd("123456789012.12345678"), "$123,456,789,012.12345678 USD");
});

test(T.label, () => {
  assert.doesNotMatch(credits("12.5"), /\$/);
  assert.match(credits("12.5"), /credits$/);
  assert.match(usd("12.5"), /^\$12\.50 USD$/);
  assert.equal(amount("12.5", "CREDIT"), credits("12.5"));
  assert.equal(amount("12.5", "USD"), usd("12.5"));
});

test(T.refuse, () => {
  assert.throws(() => credits(12.5 as unknown as string));
  assert.throws(() => credits("1e3"));
  assert.throws(() => usd("NaN"));
  assert.throws(() => amount("1", "PROVIDER_USD" as "USD"));
});

test(T.signed, () => {
  assert.equal(signedAmount("10000.00000000", "CREDIT"), "+10,000.00 credits");
  assert.equal(signedAmount("-0.00012345", "CREDIT"), "-0.00012345 credits");
  assert.equal(signedAmount("-2.5", "USD"), "-$2.50 USD");
  assert.equal(signedAmount("0", "CREDIT"), "0.00 credits");
});
