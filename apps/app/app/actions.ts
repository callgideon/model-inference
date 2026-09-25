"use server";

/**
 * The console's shared server actions (C3A). Pages call these; none of them writes on its own.
 *
 * Every rule (Origin check before anyone is resolved, the server-side context or operator flag,
 * refresh only after the database acknowledged the change) lives in `consoleActions`
 * (`lib/services/actions.ts`, tested there). This file only supplies Next's request APIs and the
 * clients; each export is one delegation. The signup grant is A2's `app/(auth)/grant.ts`.
 */

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ApiKeyCreated, ApiKeyCreateInput, ApiKeySummary, Result } from "@/lib/contracts/types";
import { consoleActions, supabaseKeyStore, type KeyClient } from "@/lib/services/actions";
import { consumerSession } from "@/lib/services/server";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";

const actions = consoleActions({
  headers,
  context: async () => (await consumerSession()).context,
  keys: async () => supabaseKeyStore((await createClient()) as unknown as KeyClient),
  session: getSession,
  endSession: async () => {
    await (await createClient()).auth.signOut();
  },
  revalidate: (path) => revalidatePath(path),
});

export async function signOut() {
  await actions.signOut();
  redirect("/login");
}

/** Create a consumer key. The secret is in this response only; pass one idempotency key per dialog. */
export async function createConsumerKey(input: ApiKeyCreateInput): Promise<Result<ApiKeyCreated>> {
  return actions.createKey(input);
}

/** Revoke one of the caller's own consumer keys (idempotent; allowed while suspended). */
export async function revokeConsumerKey(keyId: string): Promise<Result<ApiKeySummary>> {
  return actions.revokeKey(keyId);
}

/** A reasoned, idempotent operator change (U3); unavailable until an audited port exists (WR-C3A-3a). */
export async function operatorAction(input: unknown): Promise<Result<{ replayed: boolean }>> {
  return actions.operator(input);
}
