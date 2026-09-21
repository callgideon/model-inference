/**
 * The credits figures the console's own pages render, as a pure function (C1).
 *
 * `lib/credits.ts` reaches Supabase through `next/headers`, so it cannot be loaded by
 * `node --test` (R48). The decisions worth testing are here instead: which source answers the
 * balance, what a missing function means versus a failing one, and the arithmetic — which runs on
 * `Money` strings through the contract's BigInt helpers, never on `Number` subtraction.
 */

import { moneyUnits, parseMoney, subMoney, tryParseMoneyUnits, ZERO_MONEY, type Money } from "../contracts/money.ts";

export type WalletSummaryRow = {
  ledger_total: number | string | null;
  reserved_total: number | string | null;
  loaded: number | string | null;
  spent: number | string | null;
};

export type PostgrestFailure = { code?: string | null; message?: string | null } | null;

export type CreditsFigures = {
  ledger_total: Money;
  reserved: Money;
  available: Money;
  loaded: Money;
  spent: Money;
};

function money(value: number | string | null | undefined): Money {
  if (value === null || value === undefined) return ZERO_MONEY;
  // A driver that hands back a float for `numeric` is a defect, but the fixed scale is known, so the
  // conversion is exact rather than a silent reinterpretation.
  return parseMoney(typeof value === "number" ? value.toFixed(8) : value);
}

function absolute(value: Money): Money {
  return moneyUnits(value) < BigInt(0) ? subMoney(ZERO_MONEY, value) : value;
}

/**
 * `available` is `ledger_total - reserved_total`, computed on scaled integers. The figure a customer
 * reads as "available" decides whether they believe they can make another request, so it is never a
 * float subtraction.
 */
export function creditsFromSummary(summary: WalletSummaryRow): CreditsFigures {
  const ledgerTotal = money(summary.ledger_total);
  const reserved = money(summary.reserved_total);
  return {
    ledger_total: ledgerTotal,
    reserved,
    available: subMoney(ledgerTotal, reserved),
    loaded: money(summary.loaded),
    // The store reports debits as negatives; the card shows a positive "spent".
    spent: absolute(money(summary.spent)),
  };
}

/**
 * The pre-D1 path: no wallet and no holds exist, so the ledger balance *is* the available balance.
 * `loaded`/`spent` are summed from the rows that were fetched for display.
 *
 * ponytail: on this path the two display figures see only the first page of the ledger, so an
 * organization with more than `pageSize` entries would see them understated (`available` never is).
 * The path disappears when D1's `org_wallet_summary` exists; it is not worth a second query now.
 */
export function creditsFromLedgerPage(total: number | string | null, rows: readonly { delta_usd: number | string }[]): CreditsFigures {
  let loadedUnits = BigInt(0);
  let spentUnits = BigInt(0);
  for (const row of rows) {
    const units = tryParseMoneyUnits(typeof row.delta_usd === "number" ? row.delta_usd.toFixed(8) : row.delta_usd);
    if (units === null) continue;
    if (units >= BigInt(0)) loadedUnits += units;
    else spentUnits -= units;
  }
  const ledgerTotal = money(total);
  return {
    ledger_total: ledgerTotal,
    reserved: ZERO_MONEY,
    available: ledgerTotal,
    loaded: summed(loadedUnits),
    spent: summed(spentUnits),
  };
}

function summed(units: bigint): Money {
  const negative = units < BigInt(0);
  const magnitude = (negative ? -units : units).toString().padStart(9, "0");
  return parseMoney(`${negative ? "-" : ""}${magnitude.slice(0, -8)}.${magnitude.slice(-8)}`);
}

/**
 * What to do with the summary call's outcome.
 *
 * The distinction matters for money: **falling back is only safe while the function does not
 * exist.** Once D1 has shipped it, an error from it means the wallet could not be read, and
 * answering with `org_balance` instead would ignore every outstanding hold and report an *inflated*
 * available balance — a number a customer would act on. So a missing function falls back, and
 * anything else is raised.
 */
export function walletSummaryOutcome(
  data: WalletSummaryRow | null | undefined,
  error: PostgrestFailure,
): { kind: "summary"; row: WalletSummaryRow } | { kind: "fallback"; reason: string } {
  if (error !== null && error !== undefined) {
    const code = error.code ?? "";
    const message = error.message ?? "";
    // PostgREST reports an unknown function as PGRST202; PostgreSQL as 42883 (undefined_function).
    const missing = code === "PGRST202" || code === "42883" || /could not find the function/i.test(message);
    if (missing) return { kind: "fallback", reason: "org_wallet_summary does not exist yet (pre-D1)" };
    throw new Error(`the wallet summary could not be read: ${code || "unknown error"}`);
  }
  if (data === null || data === undefined) {
    return { kind: "fallback", reason: "no wallet row for this organization" };
  }
  return { kind: "summary", row: data };
}
