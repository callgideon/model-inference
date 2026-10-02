"use server";

/**
 * The console's shared server actions (C3A over infrx-api, AP-09 09b). Pages call these; none of
 * them writes on its own: every change is one API call as the signed-in user.
 *
 * Every rule (Origin check before anything runs, refresh only after the API acknowledged the
 * change) lives in `consoleActions` (`lib/services/actions.ts`, tested there). This file only
 * supplies Next's request APIs and the request's client; each export is one delegation. The signup
 * grant is A2's `app/(auth)/grant.ts`; sign-out is the auth facade's (`app/(auth)/session.ts`).
 */

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ApiKeyCreated, ApiKeyCreateInput, ApiKeySummary, FeedbackInput, Result } from "@/lib/contracts/types";
import { consoleActions, type FeedbackAck } from "@/lib/services/actions";
import { apiOperatorPort } from "@/app/(console)/admin/operator-port";
import { endSession } from "@/app/(auth)/session";
import { apiSource } from "@/lib/request-api";
import { getSession } from "@/lib/session";

const api = async () => (await apiSource()).api;

const actions = consoleActions({
  headers,
  api,
  session: getSession,
  endSession,
  revalidate: (path) => revalidatePath(path),
  operator: apiOperatorPort(api),
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

/** A reasoned, idempotent operator change (U3): one audited `/operator/v1/*` action as the signed-in operator. */
export async function operatorAction(input: unknown): Promise<Result<{ replayed: boolean }>> {
  return actions.operator(input);
}

/** C3F: one feedback signal on one of the caller's own requests (idempotent per key). */
export async function submitFeedback(input: FeedbackInput): Promise<Result<FeedbackAck>> {
  return actions.submitFeedback(input);
}
