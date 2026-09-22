/**
 * The available balance the console sidebar renders, as a pure function (C1; S1-fix B1).
 *
 * `lib/credits.ts` reaches Supabase through `next/headers`, so it cannot be loaded by
 * `node --test` (R48). The decisions worth testing are here instead:
 *
 * - A wallet read that did not answer is **unavailable**, never the legacy `org_balance`. That
 *   function is the ledger total, so it ignores every outstanding hold and reports an *inflated*
 *   available balance — a number a customer acts on. Migrations 0003–0005 ship
 *   `org_wallet_summary`, so a deployment without it is a defect to surface, not to paper over.
 * - Unavailable is *returned*, never thrown: the console layout reads this on every page and has no
 *   error boundary, so a thrown read error is a 500 on the whole console.
 * - The arithmetic runs on `Money` strings through the contract's BigInt helpers, never on `Number`.
 */

import { displayMoney, parseMoney, subMoney, ZERO_MONEY, type Money } from "../contracts/money.ts";

/** Server only, at module scope; see the note in `./query.ts`. */
if (typeof window !== "undefined") throw new Error("lib/services/credits.ts is server-only");

export type WalletSummaryRow = {
  ledger_total: number | string | null;
  reserved_total: number | string | null;
};

export type PostgrestFailure = { code?: string | null; message?: string | null } | null;

/**
 * The exact available balance, or the fact that there is no answer. There is no third case: a number
 * reaches the sidebar only when `org_wallet_summary` produced it.
 */
export type BalanceResult = { kind: "ok"; money: Money } | { kind: "unavailable"; reason: string };

/**
 * See `lib/services/console.ts`: a double holds at most 2^53 units of 1e-8, so above about 2^26
 * dollars `toFixed(8)` invents the last digits rather than converting them. Money crosses as text
 * (R59-9); a number that large is refused instead of rounded into the balance.
 */
const SAFE_MONEY_NUMBER = Math.pow(2, 26);

function amount(value: number | string | null | undefined): Money {
  if (value === null || value === undefined) return ZERO_MONEY;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Math.abs(value) >= SAFE_MONEY_NUMBER) {
      throw new TypeError("a wallet amount this large must arrive as a decimal string, not a number");
    }
    return parseMoney(value.toFixed(8));
  }
  return parseMoney(value);
}

/**
 * What the wallet summary call means for the sidebar.
 *
 * `available` is `ledger_total - reserved_total`, computed on scaled integers: the figure a customer
 * reads as "available" decides whether they believe they can make another request, so it is never a
 * float subtraction, and an outstanding hold always lowers it.
 */
export function balanceOutcome(
  data: WalletSummaryRow | null | undefined,
  error: PostgrestFailure,
): BalanceResult {
  if (error !== null && error !== undefined) {
    // Every code, including PGRST202/42883 ("no such function"): the wallet is what answers, or
    // nothing does. The code is kept for a server log, not for the page.
    return { kind: "unavailable", reason: `the wallet summary could not be read: ${error.code ?? "unknown error"}` };
  }
  if (data === null || data === undefined) {
    // A deployed summary function returning no row must not be read as a wallet with nothing held.
    return { kind: "unavailable", reason: "the wallet summary returned no wallet row" };
  }
  try {
    return { kind: "ok", money: subMoney(amount(data.ledger_total), amount(data.reserved_total)) };
  } catch {
    return { kind: "unavailable", reason: "the wallet summary is not an exact amount" };
  }
}

/**
 * What the sidebar shows: the exact amount, or `null` for the fixed "balance unavailable" copy.
 * Never a zero — a zero is an amount, and a customer who has credit would act on being shown none.
 */
export function sidebarBalance(result: BalanceResult): string | null {
  return result.kind === "ok" ? displayMoney(result.money) : null;
}
