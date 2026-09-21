import { createClient } from "@/lib/supabase/server";
import type { LedgerRow } from "@/lib/types";
import { MAX_PAGE_LIMIT } from "@/lib/contracts/types";

export type Credits = {
  rows: LedgerRow[];
  available: number;
  loaded: number;
  spent: number;
  /** Outstanding reservations: money committed to accepted work and not available to spend again. */
  reserved: number;
};

type WalletSummary = {
  ledger_total: number | string | null;
  reserved_total: number | string | null;
  loaded: number | string | null;
  spent: number | string | null;
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
 * session resolved and adds no organization selection of its own.
 */
export async function getCredits(orgId: string): Promise<Credits> {
  const supabase = await createClient();
  const [summary, ledger] = await Promise.all([
    supabase.rpc("org_wallet_summary", { p_org: orgId }).maybeSingle<WalletSummary>(),
    supabase
      .from("credit_ledger")
      .select("id, delta_usd, kind, reason, ref, created_at")
      .eq("org_id", orgId)
      .order("created_at", { ascending: false })
      .limit(MAX_PAGE_LIMIT),
  ]);

  const rows = (ledger.data ?? []) as LedgerRow[];
  const wallet = summary.data;
  if (wallet !== null && wallet !== undefined) {
    const reserved = Number(wallet.reserved_total ?? 0);
    return {
      rows,
      loaded: Number(wallet.loaded ?? 0),
      spent: Math.abs(Number(wallet.spent ?? 0)),
      reserved,
      available: Number(wallet.ledger_total ?? 0) - reserved,
    };
  }

  // Expand/contract compatibility (03 §Rollback): `wallets`, `credit_holds` and the reconciled
  // `org_wallet_summary` function are D1's migration. Against the schema deployed today there are no
  // holds at all, so the ledger balance *is* the available balance, and the existing `org_balance`
  // function still answers it without pulling every row into the application.
  const { data: balance } = await supabase.rpc("org_balance", { p_org: orgId });
  let loaded = 0;
  let spent = 0;
  for (const row of rows) {
    const value = Number(row.delta_usd);
    if (value >= 0) loaded += value;
    else spent -= value;
  }
  return { rows, loaded, spent, reserved: 0, available: Number(balance ?? 0) };
}
