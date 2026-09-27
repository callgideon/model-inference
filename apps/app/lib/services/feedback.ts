/**
 * C3F: the consumer's own-feedback action (09 §C3F, FEEDBACK-ACK). Narrow and additive: it is
 * reachable from no page or navigation until the coordinator composes it (WR-C3F-2).
 *
 * The database is the authority for everything that is not the signal itself. The door,
 * `public.submit_feedback` (WR-C3F-1, over D6F's `infrx.accept_feedback`), runs on the individual's
 * OWN session: the org is the request's, and only when the caller is one of its members (another
 * org's or an unknown request is `not_found`); the author is `auth.uid()`, the channel `console`, the
 * role `customer`, the operator marker `is_operator()`, the idempotency digest its own. Suspension,
 * replay versus conflict and the `feedback` flag (off by default: 0A000) are 0028's. So this adapter
 * refuses a smuggled provenance field before calling, forwards the allowlisted signal, and
 * acknowledges only the row the database says it stored - and only when that row is the customer
 * console signal it must be, so a label can never come back dressed as this action's answer.
 */

if (typeof window !== "undefined") throw new Error("lib/services/feedback.ts is server-only");

import { FEEDBACK_INPUT_FIELDS, type ErrorCode, type FeedbackEntry, type Result } from "../contracts/types.ts";
import type { ConsumerContext } from "../contracts/v2/consumer.ts";
import { __testables } from "./console.ts";

export const SUBMIT_FEEDBACK_RPC = "submit_feedback";

type DbError = { code?: string | null; message?: string | null };
/** The part of a supabase-js client this uses; the request's cookie client fits. */
export type FeedbackRpc = {
  rpc(name: string, args: { p_args: Record<string, unknown> }): PromiseLike<{ data: unknown; error: DbError | null }>;
};

const UNKNOWN = "your feedback could not be confirmed; send it again - the same submission is recorded once";
const REFUSED: Partial<Record<string, string>> = {
  not_found: "no such request for this account",
  invalid_request: "that feedback does not fit the request's signal",
  idempotency_conflict: "this submission key was already used for different feedback",
  org_suspended: "this account is suspended; feedback cannot be sent",
};

function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

function refusal<T>(error: DbError): Result<T> {
  if (error.code === "42501") return fail("forbidden", "this account cannot send feedback");
  const code = /^(\w+): /.exec(error.message ?? "")?.[1];
  const message = code === undefined ? undefined : REFUSED[code];
  return message === undefined ? fail("dependency_unavailable", UNKNOWN) : fail(code as ErrorCode, message);
}

/** The stored row as the DTO, or null when it is not a customer's console signal. */
function entryOf(row: unknown): FeedbackEntry | null {
  const r = row as Record<string, unknown>; // no row throws below: unconfirmed
  if (r.author_role !== "customer" || r.channel !== "console" || r.calibration_set !== false || r.rubric_version !== null) return null;
  if (typeof r.feedback_id !== "string" || r.name === "calibration_label") return null;
  return {
    id: r.feedback_id, request_id: r.request_id as string, created_at: r.created_at as string, channel: "console",
    author_role: "customer", author_principal: r.author_principal as string, name: r.name as FeedbackEntry["name"],
    value: r.value as FeedbackEntry["value"], comment: r.comment as string | null, calibration_set: false, rubric_version: null,
  };
}

/** Submit one feedback signal on one of the caller's own requests. */
export async function submitOwnFeedback(context: ConsumerContext, client: FeedbackRpc, input: unknown): Promise<Result<FeedbackEntry>> {
  const rejected = __testables.badInput<FeedbackEntry>(input, FEEDBACK_INPUT_FIELDS);
  if (rejected !== null) return rejected;
  if (context.state === "unavailable") return fail("dependency_unavailable", "your account could not be checked right now; try again");
  if (context.state !== "ready") return fail("forbidden", "sign in with a verified account first");
  const { request_id, name, value, comment, idempotency_key } = input as Record<string, unknown>;
  try {
    const { data, error } = await client.rpc(SUBMIT_FEEDBACK_RPC, { p_args: { request_id, name, value, comment, idempotency_key } });
    if (error !== null) return refusal(error);
    const entry = entryOf(data);
    return entry === null ? fail("internal_error", UNKNOWN) : { ok: true, value: entry };
  } catch {
    return fail("dependency_unavailable", UNKNOWN);
  }
}
