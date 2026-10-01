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
  l01: "C3F-L01 the review reads through the one named door with the selected workspace and the request, no identity",
  l02: "C3F-L02 a malformed request id is not_found without asking the database",
  l03: "C3F-L03 the door's refusals keep their meaning; anything unexpected is unavailable, never an empty review",
  l04: "C3F-L04 a row that is not a shared customer signal fails the whole review closed",
  l05: "C3F-L05 an uppercase or non-v4 request id reaches the door (shapes UUID_ANY_RE, LAB-10)",
  b01: "L1-B01 every page, route, provider layout and server action calls the provider guard",
};

const MUTANTS = [
  m("C3F-LX01", "the door name drifts", R, '"lab_review_feedback"', '"lab_feedback"', [C.l01]),
  m("C3F-LX02", "the review names another workspace than the selected one", R, "provider_org_id: workspace.providerId,", 'provider_org_id: "b0000009-0000-4000-8000-000000000009",', [C.l01]),
  m("C3F-LX03", "the review sends an identity", R, "request_id: requestId } }", 'request_id: requestId, user_id: workspace.providerId } }', [C.l01]),
  m("C3F-LX04", "a malformed request id reaches the database", R, '  if (typeof requestId !== "string" || !UUID.test(requestId)) return { ok: false, reason: "not_found" };\n', "", [C.l02]),
  m("C3F-LX05", "a request id with trailing text passes", SHAPES, "[0-9a-f]{12}$/i", "[0-9a-f]{12}/i", [C.l02]),
  m("C3F-LX06", "not_found reads as unavailable", R, 'if (code === "not_found") return { ok: false, reason: "not_found" };', "", [C.l03]),
  m("C3F-LX07", "a denied role reads as unavailable", R, 'if (code === "forbidden" || code === "42501")', 'if (code === "forbidden")', [C.l03]),
  m("C3F-LX08", "a role refusal reads as unavailable", R, 'if (code === "forbidden" || code === "42501")', 'if (code === "42501")', [C.l03]),
  m("C3F-LX09", "an unexpected refusal reads as forbidden", R, '      return { ok: false, reason: "unavailable" };\n    }', '      return { ok: false, reason: "forbidden" };\n    }', [C.l03]),
  m("C3F-LX10", "a lost answer reads as an empty review", R, '  } catch {\n    return { ok: false, reason: "unavailable" };', "  } catch {\n    return { ok: true, entries: [] };", [C.l03, C.l04]),
  m("C3F-LX11", "a non-list answer passes as a list with no rows", R, "(data as unknown[]).every(", "(Array.isArray(data) ? data : []).every(", [C.l04]),
  m("C3F-LX12", "an extra column (an identity) is accepted", R, "Object.keys(r).length === KEYS.length && ", "", [C.l04]),
  m("C3F-LX13", "a missing column is accepted", R, 'KEYS.every((k) => k in r) && typeof r.feedback_id === "string"', 'typeof r.feedback_id === "string"', [C.l04]),
  m("C3F-LX14", "a row without an id is accepted", R, ' && typeof r.feedback_id === "string" &&', " &&", [C.l04]),
  m("C3F-LX15", "another request's row is accepted", R, "r.request_id === requestId && ", "", [C.l04]),
  m("C3F-LX16", "a calibration label is reviewed", R, "NAMES.includes(r.name as string) && ", "", [C.l04]),
  m("C3F-LX17", "an operator-authored row is reviewed", R, '["customer", "judge"].includes(r.author_role as string)', '["customer", "judge", "operator"].includes(r.author_role as string)', [C.l04]),
  m("C3F-LX18", "a judge's signal is refused", R, '["customer", "judge"].includes(r.author_role as string)', '["customer"].includes(r.author_role as string)', [C.l04]),
  m("C3F-LX19", "an unknown channel is reviewed", R, ' &&\n    ["api", "console"].includes(r.channel as string);', ";", [C.l04]),
  m("C3F-LX20", "the review action skips the provider guard", A, "const workspace = await requireProviderWorkspace();", 'const workspace = { providerId: requestId, providerName: "", role: "developer" as const };', [C.b01]),
  m("C3F-LX21", "the review imports the record-id strictness (lowercase v4)", R, "import { UUID_ANY_RE as UUID }", "import { UUID_RE as UUID }", [C.l05]),
];

// C3F cases only must be named; L1-B01 is L1's, named here as the action guard's kill.
process.exit(await runMutants({ suite: SUITE, prefix: "C3F", mutants: MUTANTS }));
