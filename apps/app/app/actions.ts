"use server";

/**
 * The console's shared server actions (C3A). Pages call these; none of them writes on its own.
 *
 * Each action re-resolves the caller server-side (C0's `consumerSession()` for consumers, the profile
 * flag for operators), refuses a cross-site request, delegates to `lib/services/actions.ts`, and
 * revalidates only after the database has acknowledged the change - no optimistic success.
 */

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ApiKeyCreated, ApiKeyCreateInput, ApiKeySummary, Result } from "@/lib/contracts/types";
import {
  createConsumerActions,
  operatorCommand,
  runOperatorCommand,
  sameOrigin,
  supabaseKeyStore,
  type GrantOutcome,
  type KeyClient,
} from "@/lib/services/actions";
import type { RpcClient } from "@/lib/services/console";
import { consumerSession } from "@/lib/services/server";
import { getSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

const actions = createConsumerActions();

async function crossSite<T>(): Promise<Result<T> | null> {
  if (sameOrigin(await headers())) return null;
  return { ok: false, error: { code: "forbidden", message: "this change must be made from the console itself" } };
}

async function keyStore() {
  return supabaseKeyStore((await createClient()) as unknown as KeyClient);
}

export async function signOut() {
  if (sameOrigin(await headers())) {
    const supabase = await createClient();
    await supabase.auth.signOut();
  }
  redirect("/login");
}

/** Create a consumer key. The secret is in this response only; pass one idempotency key per dialog. */
export async function createConsumerKey(input: ApiKeyCreateInput): Promise<Result<ApiKeyCreated>> {
  const refused = await crossSite<ApiKeyCreated>();
  if (refused !== null) return refused;
  const { context } = await consumerSession();
  const result = await actions.createKey(context, await keyStore(), input);
  if (result.ok) revalidatePath("/api-keys");
  return result;
}

/** Revoke one of the caller's own consumer keys (idempotent; allowed while suspended). */
export async function revokeConsumerKey(keyId: string): Promise<Result<ApiKeySummary>> {
  const refused = await crossSite<ApiKeySummary>();
  if (refused !== null) return refused;
  const { context } = await consumerSession();
  const result = await actions.revokeKey(context, await keyStore(), keyId);
  if (result.ok) revalidatePath("/api-keys");
  return result;
}

/** Claim (or re-read) the one-time individual signup grant for the signed-in, verified user. */
export async function claimSignupGrant(): Promise<Result<GrantOutcome>> {
  const refused = await crossSite<GrantOutcome>();
  if (refused !== null) return refused;
  const { context } = await consumerSession();
  const result = await actions.claimGrant(context, () => createAdminClient() as unknown as RpcClient);
  if (result.ok) revalidatePath("/", "layout");
  return result;
}

/**
 * A reasoned, idempotent operator change (U3). Authority is the session's profile flag; the audit
 * actor is the session. No App-reachable audited port exists yet, so this answers unavailable.
 */
export async function operatorAction(input: unknown): Promise<Result<{ replayed: boolean }>> {
  const refused = await crossSite<{ replayed: boolean }>();
  if (refused !== null) return refused;
  const session = await getSession();
  const command = operatorCommand(session, input);
  if (!command.ok) return command;
  return runOperatorCommand(command.value, null);
}
