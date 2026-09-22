import { createClient } from "@/lib/supabase/server";
import type { Money } from "@/lib/contracts/money";
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
  available: Money;
  loaded: Money;
  spent: Money;
  /** Outstanding reservations: money committed to accepted work and not available to spend again. */
  reserved: Money;
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
 * All figures remain exact Money strings through rendering. These are legacy USD amounts.
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

  if (ledger.error) throw new Error("The ledger could not be read");
  const rows = (ledger.data ?? []) as LedgerRow[];
  const outcome = walletSummaryOutcome(summary.data, summary.error);
  let figures;
  if (outcome.kind === "summary") {
    figures = creditsFromSummary(outcome.row);
  } else {
    // Only a missing summary function permits this pre-D1 compatibility path.
    const legacy = await supabase.rpc("org_balance", { p_org: orgId });
    if (legacy.error) throw new Error("The legacy balance could not be read");
    figures = creditsFromLedgerPage(legacy.data, rows);
  }

  return {
    rows,
    loaded: figures.loaded,
    spent: figures.spent,
    reserved: figures.reserved,
    available: figures.available,
  };
}

/** Available legacy USD, including reservations; keep this out of the session/action import graph. */
export async function getBalance(orgId: string): Promise<Money> {
  return (await getCredits(orgId)).available;
}
