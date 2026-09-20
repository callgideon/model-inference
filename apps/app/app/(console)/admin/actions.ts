"use server";

import { revalidatePath } from "next/cache";
import { getSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase/admin";

const KINDS = ["grant", "purchase", "usage", "adjustment"] as const;

/** Operators only: write a credit_ledger row with the service role (RLS blocks inserts). */
export async function addCredit(formData: FormData): Promise<void> {
  const session = await getSession();
  if (!session.isOperator) throw new Error("not an operator");

  const orgId = String(formData.get("org_id") ?? "");
  const delta = Number(formData.get("delta_usd"));
  const kind = String(formData.get("kind") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();

  if (!orgId) throw new Error("pick an organization");
  if (!Number.isFinite(delta) || delta === 0) throw new Error("delta must be a non-zero number");
  if (!(KINDS as readonly string[]).includes(kind)) throw new Error("unknown kind");

  const { error } = await createAdminClient()
    .from("credit_ledger")
    .insert({
      org_id: orgId,
      delta_usd: delta,
      kind,
      reason: reason || null,
      created_by: session.userId,
    });
  if (error) throw new Error(error.message);

  revalidatePath("/admin");
}
