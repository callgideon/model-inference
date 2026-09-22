import { createClient } from "@/lib/supabase/server";
import { balanceOutcome, type BalanceResult, type WalletSummaryRow } from "@/lib/services/credits";

export type { BalanceResult };
export { sidebarBalance } from "@/lib/services/credits";

/**
 * The organization's available balance for the console sidebar.
 *
 * `available` is the reconciled wallet summary — `ledger_total - reserved_total`, summed in the
 * database — not a sum of a ledger page fetched into the application: an outstanding hold is money
 * already committed to accepted work, and a balance that ignores it invites a second admission
 * against the same credit (01-contracts, "Balance view exposes ledger total, reserved total and
 * available"). The ledger itself is paged through `ConsoleServices.ledger`.
 *
 * The tenant is the caller's own organization: this function takes the org id the session resolved
 * and adds no organization selection of its own.
 *
 * It returns a result and never throws. Every console page renders through the layout that calls it,
 * and there is no `error.tsx`, so a thrown read error would be a 500 on the whole console; an
 * unreadable wallet shows fixed "unavailable" copy instead. The figures stay exact `Money` strings
 * through rendering. These are legacy USD amounts.
 */
export async function getBalance(orgId: string): Promise<BalanceResult> {
  try {
    const supabase = await createClient();
    const summary = await supabase
      .rpc("org_wallet_summary", { p_org: orgId })
      .maybeSingle<WalletSummaryRow>();
    return balanceOutcome(summary.data, summary.error);
  } catch {
    // The client or the transport itself failed rather than answering with an error row.
    return { kind: "unavailable", reason: "the wallet summary could not be read" };
  }
}
