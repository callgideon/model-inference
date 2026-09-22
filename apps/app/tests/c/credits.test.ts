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
  // The bound is on the magnitude: a debit is negative, and `-123456789012.12345678` fabricates its
  // last digits exactly as the positive value does.
  for (const field of ["ledger_total", "reserved_total", "loaded", "spent"] as const) {
    const row = { ledger_total: "0", reserved_total: "0", loaded: "0", spent: "0" };
    assert.throws(
      () => creditsFromSummary({ ...row, [field]: -123456789012.12345678 }),
      /decimal string/,
      `${field} as a large negative number`,
    );
    assert.throws(
      () => creditsFromSummary({ ...row, [field]: -(2 ** 26) }),
      /decimal string/,
      `${field} at the negative bound`,
    );
  }
  // And just inside it, on the negative side, a number is still exact.
  const negative = creditsFromSummary({ ledger_total: "0", reserved_total: "0", loaded: "0", spent: -(2 ** 26 - 1) });
  assert.equal(negative.spent, "67108863.00000000");
  const inside = creditsFromSummary({ ledger_total: 2 ** 26 - 1, reserved_total: 0.5, loaded: 1.25, spent: -0.25 });
  assert.equal(inside.ledger_total, "67108863.00000000");
  assert.equal(inside.available, "67108862.50000000");
  assert.equal(inside.spent, "0.25000000");
  // The legacy fallback path is the one that still hands over numbers, and it is far below the bound.
  const fallback = creditsFromLedgerPage(12.5, [{ delta_usd: 20 }, { delta_usd: -7.5 }]);
  assert.equal(fallback.available, "12.50000000");
  assert.equal(fallback.loaded, "20.00000000");
  assert.equal(fallback.spent, "7.50000000");

  // A row it cannot read exactly is skipped rather than counted with invented digits: these two figures
  // are display-only on a path that disappears with D1, and a fabricated total is worse than a low one.
  const unsafe = creditsFromLedgerPage("5.00000000", [
    { delta_usd: 20 },
    { delta_usd: 123456789012.12345678 },
    { delta_usd: -(2 ** 26) },
    { delta_usd: "not-money" as unknown as number },
  ]);
  assert.equal(unsafe.loaded, "20.00000000", "the unsafe credit is skipped, not fabricated");
  assert.equal(unsafe.spent, "0.00000000", "and so is the unsafe debit");
  assert.equal(unsafe.available, "5.00000000", "while the balance comes from the function, unaffected");
});

test("a missing function falls back; a broken one does not", () => {
  // Pre-D1 the function does not exist, and the ledger balance is the available balance. Both the
  // code and the function's name are required, so neither half alone decides.
  for (const error of [
    { code: "PGRST202", message: "Could not find the function public.org_wallet_summary(p_org) in the schema cache" },
    { code: "42883", message: "function public.org_wallet_summary(uuid) does not exist" },
  ]) {
    const outcome = walletSummaryOutcome(null, error);
    assert.equal(outcome.kind, "fallback", `${String(error.code)} must fall back`);
  }

  // The message alone must not decide: an error whose text an operator or a caller can influence
  // would otherwise force the hold-ignoring fallback.
  for (const error of [
    { code: null, message: "Could not find the function in the schema cache" },
    { code: "P0001", message: "org_wallet_summary is temporarily unavailable, use org_balance" },
    { code: "PGRST301", message: "org_wallet_summary: JWT expired" },
  ]) {
    assert.throws(
      () => walletSummaryOutcome(null, error),
      /wallet summary could not be read/,
      `${String(error.code)} must not be talked into the fallback`,
    );
  }

  // And the code alone must not decide either: 42883 is also what a missing function *inside* the
  // shipped one raises, which is a broken wallet rather than an absent one.
  for (const message of [
    "function infrx.wallet_of(uuid) does not exist",
    // An inner helper whose name merely starts with the function's: the regex is anchored on the call.
    "function public.org_wallet_summary_inner(uuid) does not exist",
    "column org_wallet_summary.reserved_total does not exist",
  ]) {
    assert.throws(
      () => walletSummaryOutcome(null, { code: "42883", message }),
      /wallet summary could not be read/,
      `a missing thing inside org_wallet_summary is not org_wallet_summary missing: ${message}`,
    );
  }

  // The two halves, one at a time, over the same message: only the code differs.
  const text = "Could not find the function public.org_wallet_summary(p_org) in the schema cache";
  assert.equal(walletSummaryOutcome(null, { code: "PGRST202", message: text }).kind, "fallback");
  assert.throws(() => walletSummaryOutcome(null, { code: "P0001", message: text }), /could not be read/);

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

  // A deployed summary function returning no row must not bypass reservations.
  assert.throws(() => walletSummaryOutcome(null, null), /missing wallet row/);
  assert.equal(
    walletSummaryOutcome({ ledger_total: "1.00000000", reserved_total: "0", loaded: "1", spent: "0" }, null).kind,
    "summary",
  );
});
