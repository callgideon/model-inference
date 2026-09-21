import { createClient } from "@/lib/supabase/server";
import type { LedgerRow } from "@/lib/types";
import { MAX_PAGE_LIMIT } from "@/lib/contracts/types";
import {
  creditsFromLedgerPage,
  creditsFromSummary,
  walletSummaryOutcome,
  type WalletSummaryRow,
} from "@/lib/services/credits";

export type Credits = {
  rows: LedgerRow[];
  available: number;
  loaded: number;
  spent: number;
  /** Outstanding reservations: money committed to accepted work and not available to spend again. */
  reserved: number;
};

/**
 * The organization's credits for the console's own pages.
 *
 * `available` is the reconciled wallet summary — `ledger_total - reserved_total` — not a sum of the
 * whole ledger fetched into the application: an outstanding hold is money already committed to
 * accepted work, and a balance that ignores it invites a second admission against the same credit
 * (01-contracts, "Balance view exposes ledger total, reserved total and available"). The totals are
 * summed in the database and the displayed rows are bounded to one page; the rest of the ledger is
 * paged through `ConsoleServices.ledger`, which is where money crosses as a decimal string.
 *
 * The tenant is the caller's own organization, exactly as before: this function takes the org id the
 * session resolved and adds no organization selection of its own. The decisions — which source
 * answers, and what a failing read means — live in `lib/services/credits.ts`, where they are tested.
 *
 * `Credits` still crosses as `number` because `components/credits-card.tsx` renders it that way and
 * is not this task's file; the typed boundary (`ConsoleServices.balances`, `.ledger`) uses `Money`.
 */
export async function getCredits(orgId: string): Promise<Credits> {
  const supabase = await createClient();
  const [summary, ledger] = await Promise.all([
    supabase.rpc("org_wallet_summary", { p_org: orgId }).maybeSingle<WalletSummaryRow>(),
    supabase
      .from("credit_ledger")
      .select("id, delta_usd, kind, reason, ref, created_at")
      .eq("org_id", orgId)
      .order("created_at", { ascending: false })
      .limit(MAX_PAGE_LIMIT),
  ]);

  const rows = (ledger.data ?? []) as LedgerRow[];
  const outcome = walletSummaryOutcome(summary.data, summary.error);
  const figures =
    outcome.kind === "summary"
      ? creditsFromSummary(outcome.row)
      : // Expand/contract compatibility (03 §Rollback): `wallets`, `credit_holds` and the reconciled
        // `org_wallet_summary` function are D1's migration. Against the schema deployed today there
        // are no holds at all, so the ledger balance *is* the available balance, and the existing
        // `org_balance` function still answers it. Once the summary function exists, a *failing* call
        // is raised rather than answered from `org_balance` — which ignores reservations and would
        // report an inflated available balance.
        creditsFromLedgerPage((await supabase.rpc("org_balance", { p_org: orgId })).data, rows);

  return {
    rows,
    loaded: Number(figures.loaded),
    spent: Number(figures.spent),
    reserved: Number(figures.reserved),
    available: Number(figures.available),
  };
}
