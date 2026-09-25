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
import type { Result } from "../contracts/types.ts";
import { availableCredit, displayCredit, parseCredit, type BalanceV2 } from "../contracts/v2/types.ts";

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

// ---------------------------------------------------------------------------
// C0: the individual's CREDIT balance (`public.console_wallet_summary`, 0008)
// ---------------------------------------------------------------------------

/**
 * One row of `console_wallet_summary(p_user)` as the exact CREDIT balance of THIS account's wallet,
 * or a refusal. Refused, never defaulted:
 * - `wallet_id` NULL is the summary's "no wallet yet" row of zeros. A context that resolved a wallet
 *   and then reads that row has raced a change it cannot explain; a zero here would be a transient
 *   answer shown as confirmed funds.
 * - another wallet, a unit other than CREDIT, a JSON number (text is the contract, R59-9; a number
 *   means the column changed type and eight digits can no longer be trusted), a missing column;
 * - a stored `available` that differs from `ledger_total - reserved_total` (v2 `availableCredit`:
 *   recomputed rather than trusted, because a drifted figure is an over-spend or a refused request).
 */
export function creditBalanceOf(row: unknown, walletId: string): Result<BalanceV2> {
  const refuse = (message: string): Result<BalanceV2> => ({ ok: false, error: { code: "internal_error", message } });
  if (typeof row !== "object" || row === null) return refuse("the wallet summary returned no row");
  const r = row as Record<string, unknown>;
  if (r.wallet_id !== walletId) return refuse("the wallet summary is not this account's wallet");
  if (r.kind !== "consumer" || r.unit !== "CREDIT") return refuse("the wallet summary is not a consumer CREDIT wallet");
  const amounts = [r.ledger_total, r.reserved_total, r.available];
  if (!amounts.every((value) => typeof value === "string")) return refuse("a wallet amount is not a decimal string");
  try {
    const balance: BalanceV2 = {
      schema_version: 2,
      wallet_id: walletId,
      kind: "consumer",
      unit: "CREDIT",
      ledger_total: parseCredit(r.ledger_total),
      reserved_total: parseCredit(r.reserved_total),
      available: parseCredit(r.available),
    };
    if (availableCredit(balance) !== balance.available) return refuse("the wallet's available balance has drifted");
    return { ok: true, value: balance };
  } catch {
    return refuse("the wallet summary is not an exact CREDIT amount");
  }
}

/** What the sidebar shows: exact credits, or `null` for fixed "balance unavailable" copy - never a zero. */
export function sidebarCredit(result: Result<BalanceV2>): string | null {
  return result.ok ? displayCredit(result.value.available) : null;
}
