// node --test "tests/**/*.test.ts"
//
// The console credits card's figures (C1 acceptance: "migrate credits to wallet/hold views and test
// current selected-org behaviour without adding org switching").
//
// `lib/credits.ts` itself reaches Supabase through `next/headers`, so it cannot be loaded here (R48).
// The decisions are in `lib/services/credits.ts`: which source answers, what a failing read means, and
// arithmetic that never touches `Number`.

import assert from "node:assert/strict";
import test from "node:test";
import { ZERO_MONEY } from "../../lib/contracts/money.ts";
import { creditsFromLedgerPage, creditsFromSummary, walletSummaryOutcome } from "../../lib/services/credits.ts";

test("available is the reconciled total minus reservations, on scaled integers", () => {
  const figures = creditsFromSummary({
    ledger_total: "10.00000003",
    reserved_total: "0.00000004",
    loaded: "12.50000000",
    spent: "-2.49999997",
  });
  // 10.00000003 - 0.00000004 in floats is 9.99999999000000x; the eighth digit is the whole point.
  assert.equal(figures.available, "9.99999999");
  assert.equal(figures.ledger_total, "10.00000003");
  assert.equal(figures.reserved, "0.00000004");
  assert.equal(figures.loaded, "12.50000000");
  assert.equal(figures.spent, "2.49999997", "a debit is shown as a positive amount spent");

  // Past 2^53 scaled units a float cannot hold the eighth digit at all: this subtraction comes back
  // as 123456789012.00000000 in `Number`, which is a cent the customer does not have.
  const large = creditsFromSummary({
    ledger_total: "123456789012.00000001",
    reserved_total: "0.00000002",
    loaded: "123456789012.00000001",
    spent: "0",
  });
  assert.equal(large.available, "123456789011.99999999");
});

test("a hold lowers what is available, and a wallet with nothing in it is three zeros", () => {
  const held = creditsFromSummary({ ledger_total: "5.00000000", reserved_total: "1.25000000", loaded: "5.00000000", spent: "0" });
  assert.equal(held.available, "3.75000000");
  const empty = creditsFromSummary({ ledger_total: null, reserved_total: null, loaded: null, spent: null });
  assert.deepEqual(
    { available: empty.available, reserved: empty.reserved, loaded: empty.loaded },
    { available: ZERO_MONEY, reserved: ZERO_MONEY, loaded: ZERO_MONEY },
  );
});

test("the pre-D1 fallback answers from the ledger, where no hold exists", () => {
  const figures = creditsFromLedgerPage("7.50000000", [
    { delta_usd: "10.00000000" },
    { delta_usd: "-2.50000000" },
    { delta_usd: -0.25 },
  ]);
  assert.equal(figures.available, "7.50000000", "the balance is the function's, not a sum of the page");
  assert.equal(figures.reserved, ZERO_MONEY, "there are no holds in that schema");
  assert.equal(figures.loaded, "10.00000000");
  assert.equal(figures.spent, "2.75000000");
});

test("a wallet amount that arrives as a number is bounded by what a double can hold", () => {
  // The same rule as the services' reader: `toFixed(8)` does not convert above ~2^26, it fabricates.
  assert.throws(
    () => creditsFromSummary({ ledger_total: 123456789012.12345678, reserved_total: "0", loaded: "0", spent: "0" }),
    /decimal string/,
    "a large float would have reported digits the organization never had",
  );
  assert.throws(
    () => creditsFromSummary({ ledger_total: 2 ** 26, reserved_total: "0", loaded: "0", spent: "0" }),
    /decimal string/,
  );
  const inside = creditsFromSummary({ ledger_total: 2 ** 26 - 1, reserved_total: 0.5, loaded: 1.25, spent: -0.25 });
  assert.equal(inside.ledger_total, "67108863.00000000");
  assert.equal(inside.available, "67108862.50000000");
  assert.equal(inside.spent, "0.25000000");
  // The legacy fallback path is the one that still hands over numbers, and it is far below the bound.
  const fallback = creditsFromLedgerPage(12.5, [{ delta_usd: 20 }, { delta_usd: -7.5 }]);
  assert.equal(fallback.available, "12.50000000");
  assert.equal(fallback.loaded, "20.00000000");
  assert.equal(fallback.spent, "7.50000000");
});

test("a missing function falls back; a broken one does not", () => {
  // Pre-D1 the function does not exist, and the ledger balance is the available balance.
  for (const error of [
    { code: "PGRST202", message: "Could not find the function public.org_wallet_summary(p_org)" },
    { code: "42883", message: "function public.org_wallet_summary(uuid) does not exist" },
    { code: null, message: "Could not find the function in the schema cache" },
  ]) {
    const outcome = walletSummaryOutcome(null, error);
    assert.equal(outcome.kind, "fallback", `${String(error.code)} must fall back`);
  }

  // Once it exists, a failing call must NOT be answered from `org_balance`: that function ignores
  // every outstanding hold, so the fallback would report an inflated available balance — a number a
  // customer would act on. An error is raised instead, and the page shows its failure state.
  for (const error of [
    { code: "42501", message: "permission denied for function org_wallet_summary" },
    { code: "57014", message: "canceling statement due to statement timeout" },
    { code: "PGRST301", message: "JWT expired" },
  ]) {
    assert.throws(
      () => walletSummaryOutcome(null, error),
      /wallet summary could not be read/,
      `${error.code} must not be answered from org_balance`,
    );
  }

  // No row and no error is a new organization: zero, through the fallback.
  assert.equal(walletSummaryOutcome(null, null).kind, "fallback");
  assert.equal(
    walletSummaryOutcome({ ledger_total: "1.00000000", reserved_total: "0", loaded: "1", spent: "0" }, null).kind,
    "summary",
  );
});
