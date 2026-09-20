"use server";

import { revalidatePath } from "next/cache";
import { generateKey, hashKey, keyPrefix } from "@/lib/keys";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";

export type CreateResult = { ok: true; key: string } | { ok: false; error: string };

/** Mints a key, stores only its SHA-256, and hands the secret back exactly once. */
export async function createApiKey(name: string): Promise<CreateResult> {
  const trimmed = name.trim();
  if (!trimmed) return { ok: false, error: "Give the key a name." };
  if (trimmed.length > 64) return { ok: false, error: "Name is too long (64 characters max)." };

  const session = await getSession();
  if (session.role !== "owner") return { ok: false, error: "Only owners can create keys." };

  const key = generateKey();
  const supabase = await createClient();
  const { error } = await supabase.from("api_keys").insert({
    org_id: session.orgId,
    created_by: session.userId,
    name: trimmed,
    prefix: keyPrefix(key),
    key_hash: await hashKey(key),
  });
  if (error) return { ok: false, error: error.message };

  revalidatePath("/api-keys");
  return { ok: true, key };
}

export async function revokeApiKey(id: string): Promise<{ error?: string }> {
  const session = await getSession();
  if (session.role !== "owner") return { error: "Only owners can revoke keys." };

  const supabase = await createClient();
  const { error } = await supabase
    .from("api_keys")
    .update({ revoked_at: new Date().toISOString() })
    .eq("id", id)
    .eq("org_id", session.orgId);
  if (error) return { error: error.message };

  revalidatePath("/api-keys");
  return {};
}
