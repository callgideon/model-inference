import { createClient } from "@/lib/supabase/server";
import type { LedgerRow } from "@/lib/types";

export type Credits = {
  rows: LedgerRow[];
  available: number;
  loaded: number;
  spent: number;
};

/** The whole ledger for an org: small by construction (one row per top-up or adjustment). */
export async function getCredits(orgId: string): Promise<Credits> {
  const supabase = await createClient();
  const { data } = await supabase
    .from("credit_ledger")
    .select("id, delta_usd, kind, reason, ref, created_at")
    .eq("org_id", orgId)
    .order("created_at", { ascending: false });

  const rows = (data ?? []) as LedgerRow[];
  let loaded = 0;
  let spent = 0;
  for (const r of rows) {
    const v = Number(r.delta_usd);
    if (v >= 0) loaded += v;
    else spent -= v;
  }
  return { rows, loaded, spent, available: loaded - spent };
}
