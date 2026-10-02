// C3F, AP-09 09c: a provider reviews the feedback a customer shared with it (09 §C3F, LAB-ACCESS) and
// records its own human review, over AP-08's `/lab/v1/traces/{id}/feedback|reviews` (replaces the
// direct lab_review_feedback RPC). The permission rule is not re-implemented here: the route runs the
// door as the signed-in user, which answers only for a current developer+ member of the selected
// provider when the request's org holds a CURRENT grant to that provider naming the job's model,
// `feedback` and `provider_sharing` (L2's rule, on the database clock). Anything else - another org's
// request, a revoked or expired grant, an unknown id - is the same 404. This adapter sends only the
// selected workspace and the request, and accepts only stored customer (or judge) signals and human
// reviews: never a label as a review, never the customer's identity.
import type { ApiError, LabApi } from "../../api/index.ts";
import { holds, type Actor } from "../../auth/access.ts";
import { UUID_ANY_RE as UUID, UUID_RE as ID } from "../shapes.ts";

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
/** A provider member's human review of one request (0064), as the route records it. */
export type HumanReview = {
  review_id: string;
  request_id: string;
  reviewer: string;
  provenance: "human";
  verdict: "pass" | "fail" | "unsure";
  comment: string | null;
  run_id: string | null;
  rubric_version: number | null;
  created_at: string;
};
export type ReviewResult = { ok: true; entries: ReviewEntry[]; reviews: HumanReview[] } | { ok: false; reason: "not_found" | "forbidden" | "unavailable" };
export type ReviewOutcome = { ok: true; review: HumanReview } | { ok: false; reason: "invalid" | "not_found" | "forbidden" | "conflict" | "unavailable" };

const KEYS = ["feedback_id", "name", "value", "comment", "author_role", "channel", "created_at"];
const NAMES = ["thumb", "rating", "correction", "comment"];
const VERDICTS = ["pass", "fail", "unsure"];

/** A stored signal shared with this provider: exactly the signal's fields (no identity), known kinds. */
function shared(row: unknown): boolean {
  const r = row as Record<string, unknown>; // a non-object row throws below: unavailable
  return Object.keys(r).length === KEYS.length && KEYS.every((k) => k in r) && typeof r.feedback_id === "string" &&
    NAMES.includes(r.name as string) && ["customer", "judge"].includes(r.author_role as string) &&
    ["api", "console"].includes(r.channel as string);
}

const text = (v: unknown) => v === null || typeof v === "string";
function human(row: unknown, requestId: string): row is HumanReview {
  const r = row as Record<string, unknown>;
  return typeof r.review_id === "string" && r.request_id === requestId && typeof r.reviewer === "string" && r.provenance === "human" &&
    VERDICTS.includes(r.verdict as string) && text(r.comment) && text(r.run_id) && (r.rubric_version === null || Number.isInteger(r.rubric_version)) &&
    typeof r.created_at === "string";
}

const refusal = (e: ApiError) => (e.status === 404 ? "not_found" : e.status === 403 ? "forbidden" : "unavailable") as "not_found" | "forbidden" | "unavailable";

export async function reviewFeedback(api: LabApi | null, workspace: Actor, requestId: unknown): Promise<ReviewResult> {
  if (typeof requestId !== "string" || !UUID.test(requestId)) return { ok: false, reason: "not_found" };
  if (api === null) return { ok: false, reason: "unavailable" };
  const answer = await api.call("get", "/lab/v1/traces/{request_id}/feedback", { params: { request_id: requestId }, query: { provider_org_id: workspace.providerId } });
  if (!answer.ok) return { ok: false, reason: refusal(answer.error) };
  try {
    // A non-list answer has no `every` and throws: unavailable.
    const { signals, reviews } = answer.data as { signals: unknown[]; reviews: unknown[] };
    if (!signals.every(shared) || !reviews.every((r) => human(r, requestId))) return { ok: false, reason: "unavailable" };
    return { ok: true, entries: signals.map((s) => ({ ...(s as Omit<ReviewEntry, "request_id">), request_id: requestId })), reviews: reviews as HumanReview[] };
  } catch {
    return { ok: false, reason: "unavailable" };
  }
}

/** A developer+ member's human review of one request. `idempotency_key` is minted when the form renders,
 *  so a double submit is one review; the reviewer and the `human` provenance are the route's. */
export async function submitReview(api: LabApi | null, w: Actor, input: Record<string, unknown>): Promise<ReviewOutcome> {
  if (!holds(w, "run_evaluation")) return { ok: false, reason: "forbidden" };
  const { request_id: request, verdict, comment, run_id: run, rubric_version: rubric, idempotency_key: key } = input;
  const version = rubric === undefined || rubric === "" ? null : typeof rubric === "string" && /^[0-9]{1,4}$/.test(rubric) ? Number(rubric) : 0;
  const note = comment === undefined || comment === "" ? null : comment;
  if (typeof request !== "string" || !UUID.test(request) || !VERDICTS.includes(verdict as string) || typeof key !== "string" || !ID.test(key)) return { ok: false, reason: "invalid" };
  if ((note !== null && (typeof note !== "string" || note.length > 2000)) || (run !== undefined && run !== "" && (typeof run !== "string" || !ID.test(run)))) return { ok: false, reason: "invalid" };
  if (version !== null && (version < 1 || version > 1000)) return { ok: false, reason: "invalid" };
  if (api === null) return { ok: false, reason: "unavailable" };
  const body = { verdict: verdict as HumanReview["verdict"], ...(note === null ? {} : { comment: note as string }), ...(run ? { run_id: run as string } : {}), ...(version === null ? {} : { rubric_version: version }) };
  const answer = await api.call("post", "/lab/v1/traces/{request_id}/reviews", { params: { request_id: request }, query: { provider_org_id: w.providerId }, idempotencyKey: key, body });
  if (!answer.ok) {
    const s = answer.error.status;
    return { ok: false, reason: s === 409 ? "conflict" : s === 422 ? "invalid" : refusal(answer.error) };
  }
  return human(answer.data, request) ? { ok: true, review: answer.data } : { ok: false, reason: "unavailable" };
}
