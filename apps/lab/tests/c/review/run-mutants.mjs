#!/usr/bin/env node
// C3F's Lab mutant runner (R32; LANE-RULES addendum): every decision of lib/services/review/ is one edit
// that a case it names must fail by assertion (L1's boundary case guards the action's guard). A stale
// `find`, a load failure or a failure in an undeclared case is not a kill. On the shared harness (tests/l/shell).
// Usage: node tests/c/review/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/c/review/review.test.ts", "tests/l/shell/boundary.test.ts"];
const R = "lib/services/review/index.ts";
const A = "lib/services/review/actions.ts";
const SHAPES = "lib/services/shapes.ts"; // LAB-10: the shared input shapes

const C = {
  l01: "C3F-L01 the review reads the request's feedback route with the selected workspace and the request, no identity",
  l02: "C3F-L02 a malformed request id is not_found without asking the API; a misconfigured Lab is unavailable",
  l03: "C3F-L03 the route's refusals keep their meaning; anything unexpected is unavailable, never an empty review",
  l04: "C3F-L04 a signal that is not a shared customer signal, or a review that is not a human one, fails the whole review closed",
  l05: "C3F-L05 an uppercase or non-v4 request id reaches the route (shapes UUID_ANY_RE, LAB-10)",
  r01: "C3F-R01 a human review posts the verdict to the request's reviews route with the form's key; provenance is the server's",
  r02: "C3F-R02 a viewer, a malformed field or a missing key is refused before any call",
  r03: "C3F-R03 the route's refusals keep their meaning; an unreadable answer is unavailable, never a recorded review",
  b01: "L1-B01 every page, route, provider layout and server action calls the provider guard",
};

const MUTANTS = [
  // AP-09 09c: the review over /lab/v1/traces/{id}/feedback|reviews (replace C3F-LX01..LX21, the RPC adapter's).
  m("C3F-LX01", "the read drifts from the feedback route", R, '"/lab/v1/traces/{request_id}/feedback"', '"/lab/v1/traces/{request_id}"', [C.l01]),
  m("C3F-LX02", "the review names another workspace than the selected one", R, "query: { provider_org_id: workspace.providerId } });", 'query: { provider_org_id: "b0000009-0000-4000-8000-000000000009" } });', [C.l01]),
  m("C3F-LX03", "the review sends an identity", R, "params: { request_id: requestId }, query: { provider_org_id: workspace.providerId } }", "params: { request_id: requestId }, query: { provider_org_id: workspace.providerId, user_id: workspace.providerId } }", [C.l01]),
  m("C3F-LX04", "a malformed request id reaches the API", R, '  if (typeof requestId !== "string" || !UUID.test(requestId)) return { ok: false, reason: "not_found" };\n', "", [C.l02]),
  m("C3F-LX05", "a request id with trailing text passes", SHAPES, "[0-9a-f]{12}$/i", "[0-9a-f]{12}/i", [C.l02]),
  m("C3F-LX22", "a misconfigured Lab reads as not found", R, '  if (api === null) return { ok: false, reason: "unavailable" };\n  const answer = await api.call("get"', '  if (api === null) return { ok: false, reason: "not_found" };\n  const answer = await api.call("get"', [C.l02]),
  m("C3F-LX06", "not_found reads as unavailable", R, 'e.status === 404 ? "not_found" :', 'e.status === 404 ? "unavailable" :', [C.l03]),
  m("C3F-LX07", "a refusal reads as unavailable", R, 'e.status === 403 ? "forbidden" :', 'e.status === 403 ? "unavailable" :', [C.l03, C.r03]),
  m("C3F-LX09", "an unexpected failure reads as forbidden", R, '"forbidden" : "unavailable") as', '"forbidden" : "forbidden") as', [C.l03]),
  m("C3F-LX10", "a lost answer reads as an empty review", R, "  } catch {\n    return { ok: false, reason: \"unavailable\" };", "  } catch {\n    return { ok: true, entries: [], reviews: [] };", [C.l03, C.l04]),
  m("C3F-LX11", "a non-list answer passes as no rows", R, "const { signals, reviews } = answer.data as { signals: unknown[]; reviews: unknown[] };",
    "const { signals = [], reviews = [] } = (answer.data ?? {}) as { signals: unknown[]; reviews: unknown[] };\n    if (!Array.isArray(signals) || !Array.isArray(reviews)) return { ok: true, entries: [], reviews: [] };", [C.l04]),
  m("C3F-LX12", "an extra field (an identity) is accepted", R, "Object.keys(r).length === KEYS.length && ", "", [C.l04]),
  m("C3F-LX13", "a missing field is accepted", R, 'KEYS.every((k) => k in r) && typeof r.feedback_id === "string"', 'typeof r.feedback_id === "string"', [C.l04]),
  m("C3F-LX14", "a signal without an id is accepted", R, ' && typeof r.feedback_id === "string" &&', " &&", [C.l04]),
  m("C3F-LX16", "a calibration label is reviewed", R, "NAMES.includes(r.name as string) && ", "", [C.l04]),
  m("C3F-LX17", "an operator-authored signal is reviewed", R, '["customer", "judge"].includes(r.author_role as string)', '["customer", "judge", "operator"].includes(r.author_role as string)', [C.l04]),
  m("C3F-LX18", "a judge's signal is refused", R, '["customer", "judge"].includes(r.author_role as string)', '["customer"].includes(r.author_role as string)', [C.l04]),
  m("C3F-LX19", "an unknown channel is reviewed", R, ' &&\n    ["api", "console"].includes(r.channel as string);', ";", [C.l04]),
  m("C3F-LX15", "another request's review is accepted", R, "typeof r.review_id === \"string\" && r.request_id === requestId && ", "typeof r.review_id === \"string\" && ", [C.l04]),
  m("C3F-LX23", "a non-human review is accepted", R, ' && r.provenance === "human" &&', " &&", [C.l04, C.r03]),
  m("C3F-LX24", "any verdict is accepted", R, "    VERDICTS.includes(r.verdict as string) && text(r.comment)", "    text(r.comment)", [C.l04]),
  m("C3F-LX25", "a review without an id is accepted", R, 'return typeof r.review_id === "string" && ', "return ", [C.l04]),
  m("C3F-LX26", "the signals pass without their reviews being checked", R, " || !reviews.every((r) => human(r, requestId))", "", [C.l04]),
  m("C3F-LX27", "a signal is shown under another request", R, "request_id: requestId })), reviews:", "request_id: \"\" })), reviews:", [C.l01, C.l04]),
  m("C3F-LX20", "the review action skips the provider guard", A, "export async function reviewRequestFeedback(requestId: string): Promise<ReviewResult> {\n  const workspace = await requireProviderWorkspace();",
    'export async function reviewRequestFeedback(requestId: string): Promise<ReviewResult> {\n  const workspace = { providerId: requestId, providerName: "", role: "developer" as const, capabilities: [] };', [C.b01]),
  m("C3F-LX28", "the review-writing action skips the provider guard", A, "export async function submitRequestReview(formData: FormData): Promise<ReviewOutcome> {\n  const workspace = await requireProviderWorkspace();",
    'export async function submitRequestReview(formData: FormData): Promise<ReviewOutcome> {\n  const workspace = { providerId: "", providerName: "", role: "developer" as const, capabilities: [] };', [C.b01]),
  m("C3F-LX21", "the review imports the record-id strictness (lowercase v4)", R, "import { UUID_ANY_RE as UUID, UUID_RE as ID }", "import { UUID_RE as UUID, UUID_RE as ID }", [C.l05]),
  // the human review (POST .../reviews)
  m("C3F-RX01", "a viewer records a review", R, '  if (!holds(w, "run_evaluation")) return { ok: false, reason: "forbidden" };\n', "", [C.r02]),
  m("C3F-RX02", "the review takes the provider from the form", R, "query: { provider_org_id: w.providerId }, idempotencyKey: key, body });", "query: { provider_org_id: (input.provider_org_id as string) ?? w.providerId }, idempotencyKey: key, body });", [C.r01]),
  m("C3F-RX03", "the review carries no key", R, "idempotencyKey: key, body });", "body });", [C.r01]),
  m("C3F-RX04", "a review without the form's key is sent", R, ' || typeof key !== "string" || !ID.test(key)) return { ok: false, reason: "invalid" };', ') return { ok: false, reason: "invalid" };', [C.r02]),
  m("C3F-RX05", "any verdict is sent", R, ' || !VERDICTS.includes(verdict as string) ||', " ||", [C.r02]),
  m("C3F-RX06", "a malformed request id is sent", R, 'if (typeof request !== "string" || !UUID.test(request) ||', 'if (typeof request !== "string" ||', [C.r02]),
  m("C3F-RX07", "an over-long comment is sent", R, "note.length > 2000", "note.length > 20000", [C.r02]),
  m("C3F-RX08", "a malformed run id is sent", R, ' || (run !== undefined && run !== "" && (typeof run !== "string" || !ID.test(run)))) return', ") return", [C.r02]),
  m("C3F-RX09", "an out-of-range rubric version is sent", R, "if (version !== null && (version < 1 || version > 1000)) return", "if (version !== null && version < 0) return", [C.r02]),
  m("C3F-RX10", "a blank comment is sent as text", R, 'const note = comment === undefined || comment === "" ? null : comment;', "const note = comment === undefined ? null : comment;", [C.r01]),
  m("C3F-RX11", "the rubric version goes as text", R, "{ rubric_version: version }", "{ rubric_version: String(version) }", [C.r01]),
  m("C3F-RX12", "the form's provenance or reviewer rides along", R, "const body = { verdict: verdict as HumanReview[\"verdict\"],", "const body = { ...input, verdict: verdict as HumanReview[\"verdict\"],", [C.r01]),
  m("C3F-RX13", "a reused key with other values reads as invalid", R, 'reason: s === 409 ? "conflict" :', 'reason: s === 409 ? "invalid" :', [C.r03]),
  m("C3F-RX14", "a rejected shape reads as unavailable", R, 's === 422 ? "invalid" : refusal(answer.error)', "refusal(answer.error)", [C.r03]),
  m("C3F-RX15", "an unreadable answer is a recorded review", R, 'return human(answer.data, request) ? { ok: true, review: answer.data } : { ok: false, reason: "unavailable" };', "return { ok: true, review: answer.data as HumanReview };", [C.r03]),
  m("C3F-RX16", "a misconfigured Lab reads as invalid", R, '  if (api === null) return { ok: false, reason: "unavailable" };\n  const body', '  if (api === null) return { ok: false, reason: "invalid" };\n  const body', [C.r02]),
];

// C3F cases only must be named; L1-B01 is L1's, named here as the action guard's kill.
process.exit(await runMutants({ suite: SUITE, prefix: "C3F", mutants: MUTANTS }));
