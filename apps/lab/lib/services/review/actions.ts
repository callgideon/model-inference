"use server";
// C3F: the Lab's review actions. The guard first (a selected provider workspace, else a 404), then the
// route as the same user's own session (AP-09): the Lab holds no service-role key and sends no identity.
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { sessionApi } from "../../auth/session.ts";
import { reviewFeedback, submitReview, type ReviewOutcome, type ReviewResult } from "./index.ts";

export async function reviewRequestFeedback(requestId: string): Promise<ReviewResult> {
  const workspace = await requireProviderWorkspace();
  return reviewFeedback(sessionApi(), workspace, requestId);
}

export async function submitRequestReview(formData: FormData): Promise<ReviewOutcome> {
  const workspace = await requireProviderWorkspace();
  return submitReview(sessionApi(), workspace, Object.fromEntries(formData));
}
