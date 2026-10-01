// C3F: a provider reviews the feedback a customer shared with it (09 §C3F, LAB-ACCESS).
// The permission rule is not re-implemented here: the door `public.lab_review_feedback` (WR-C3F-1)
// runs on the user's own session and answers only for a current developer+ member of the selected
// provider when the request's org holds a CURRENT grant to that provider naming the job's model,
// `feedback` and `provider_sharing` (L2's rule, on the database clock). Anything else - another org's
// request, a revoked or expired grant, an unknown id - is the same `not_found`. This adapter sends
// only the selected workspace and the request, and accepts only stored customer (or judge) signals:
// never a label, never the customer's identity.
import type { Membership } from "../../auth/access.ts";
import { UUID_ANY_RE as UUID } from "../shapes.ts";

export const REVIEW_RPC = "lab_review_feedback";
export type ReviewRpc = {
  rpc(name: string, args: { p_args: Record<string, string> }): PromiseLike<{ data: unknown; error: { code?: string | null; message?: string | null } | null }>;
};

export type ReviewEntry = {
  feedback_id: string;
  request_id: string;
  name: "thumb" | "rating" | "correction" | "comment";
  value: boolean | number | string;
  comment: string | null;
  author_role: "customer" | "judge";
  channel: "api" | "console";
  created_at: string;
};
export type ReviewResult = { ok: true; entries: ReviewEntry[] } | { ok: false; reason: "not_found" | "forbidden" | "unavailable" };

const KEYS = ["feedback_id", "request_id", "name", "value", "comment", "author_role", "channel", "created_at"];
const NAMES = ["thumb", "rating", "correction", "comment"];

function shared(row: unknown, requestId: string): row is ReviewEntry {
  const r = row as Record<string, unknown>; // a non-object row throws below: unavailable
  return Object.keys(r).length === KEYS.length && KEYS.every((k) => k in r) && typeof r.feedback_id === "string" &&
    r.request_id === requestId && NAMES.includes(r.name as string) && ["customer", "judge"].includes(r.author_role as string) &&
    ["api", "console"].includes(r.channel as string);
}

export async function reviewFeedback(client: ReviewRpc, workspace: Membership, requestId: unknown): Promise<ReviewResult> {
  if (typeof requestId !== "string" || !UUID.test(requestId)) return { ok: false, reason: "not_found" };
  try {
    const { data, error } = await client.rpc(REVIEW_RPC, { p_args: { provider_org_id: workspace.providerId, request_id: requestId } });
    if (error) {
      const code = /^(\w+): /.exec(error.message ?? "")?.[1] ?? error.code;
      if (code === "not_found") return { ok: false, reason: "not_found" };
      if (code === "forbidden" || code === "42501") return { ok: false, reason: "forbidden" };
      return { ok: false, reason: "unavailable" };
    }
    // A non-list answer has no `every` and throws: unavailable.
    if (!(data as unknown[]).every((row) => shared(row, requestId))) return { ok: false, reason: "unavailable" };
    return { ok: true, entries: data as ReviewEntry[] };
  } catch {
    return { ok: false, reason: "unavailable" };
  }
}
