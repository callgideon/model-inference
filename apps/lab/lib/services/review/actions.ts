"use server";
// C3F: the Lab's review action. The guard first (a selected provider workspace, else a 404), then the
// door on the same user's own session client: the Lab holds no service-role key and sends no identity.
import { labConfig } from "../../auth/config.ts";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { readOnlyClient } from "../../auth/session.ts";
import { reviewFeedback, type ReviewResult, type ReviewRpc } from "./index.ts";

export async function reviewRequestFeedback(requestId: string): Promise<ReviewResult> {
  const workspace = await requireProviderWorkspace();
  const config = labConfig(process.env);
  if (config === null) return { ok: false, reason: "unavailable" };
  // Read-only here: the guard's client already refreshed the session cookies for this request.
  const client = await readOnlyClient(config);
  return reviewFeedback(client as unknown as ReviewRpc, workspace, requestId);
}
