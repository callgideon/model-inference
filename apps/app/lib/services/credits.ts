/**
 * The credits figures the console's own pages render, as a pure function (C1).
 *
 * `lib/credits.ts` reaches Supabase through `next/headers`, so it cannot be loaded by
 * `node --test` (R48). The decisions worth testing are here instead: which source answers the
 * balance, what a missing function means versus a failing one, and the arithmetic — which runs on
 * `Money` strings through the contract's BigInt helpers, never on `Number` subtraction.
 */

import {
  moneyFromUnits,
  moneyUnits,
  parseMoney,
  subMoney,
  tryParseMoneyUnits,
  ZERO_MONEY,
  type Money,
} from "../contracts/money.ts";

/** Server only, at module scope; see the note in `./query.ts`. */
if (typeof window !== "undefined") throw new Error("lib/services/credits.ts is server-only");

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

/**
 * See `lib/services/console.ts`: a double holds at most 2^53 units of 1e-8, so above about 2^26
 * dollars `toFixed(8)` invents the last digits rather than converting them. Money crosses as text
 * (R59-9); the only number that still arrives is the legacy `org_balance` fallback's, far below this.
 */
const SAFE_MONEY_NUMBER = Math.pow(2, 26);

function money(value: number | string | null | undefined): Money {
  if (value === null || value === undefined) return ZERO_MONEY;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Math.abs(value) >= SAFE_MONEY_NUMBER) {
      throw new TypeError("a wallet amount this large must arrive as a decimal string, not a number");
    }
    return parseMoney(value.toFixed(8));
  }
  return parseMoney(value);
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
    const units =
      typeof row.delta_usd === "number"
        ? Math.abs(row.delta_usd) < SAFE_MONEY_NUMBER
          ? tryParseMoneyUnits(row.delta_usd.toFixed(8))
          : null
        : tryParseMoneyUnits(row.delta_usd);
    if (units === null) continue;
    if (units >= BigInt(0)) loadedUnits += units;
    else spentUnits -= units;
  }
  const ledgerTotal = money(total);
  return {
    ledger_total: ledgerTotal,
    reserved: ZERO_MONEY,
    available: ledgerTotal,
    loaded: moneyFromUnits(loadedUnits),
    spent: moneyFromUnits(spentUnits),
  };
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
    /**
     * Both halves are required, and each one catches something the other does not.
     *
     * The **code** must be one of the two that mean "no such function" — PostgREST's `PGRST202` or
     * PostgreSQL's `42883` — because an error whose *text* merely mentions the function (a `P0001`
     * raised inside it, say) would otherwise force the fallback. The **name** must appear too,
     * because `42883` is also what a missing function *inside* the shipped one raises, and that is a
     * broken wallet rather than an absent one. Falling back answers from `org_balance`, which ignores
     * every outstanding hold: getting this wrong reports an inflated available balance, which is a
     * number a customer acts on.
     */
    // Anchored on the call, so a helper named `org_wallet_summary_inner(uuid)` going missing inside the
    // shipped function is not read as the shipped function going missing.
    const missing = (code === "PGRST202" || code === "42883") && /org_wallet_summary\s*\(/.test(message);
    if (missing) return { kind: "fallback", reason: "org_wallet_summary does not exist yet (pre-D1)" };
    throw new Error(`the wallet summary could not be read: ${code || "unknown error"}`);
  }
  if (data === null || data === undefined) {
    return { kind: "fallback", reason: "no wallet row for this organization" };
  }
  return { kind: "summary", row: data };
}
