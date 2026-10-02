#!/usr/bin/env node
// C3L's mutant runner (R32; LANE-RULES addendum): every C3L decision is one edit that a case it names
// must fail by assertion, on the shared harness (tests/l/shell); L1's boundary suite runs
// too, because it is what proves each judge server action calls the provider guard itself.
// Usage: node tests/c/judge/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/c/judge/judge.test.ts", "tests/c/judge/runs.test.ts", "tests/c/judge/page.test.ts", "tests/l/shell/boundary.test.ts"];

const CORE = "lib/services/judge/core.ts";
const ACTIONS = "lib/services/judge/actions.ts";
const RUNS = "lib/services/judge/runs.ts";
const SHAPES = "lib/services/shapes.ts"; // LAB-10: the shared input shapes
const PAGE = "app/(provider)/judge/page.tsx";
const FORM = "app/(provider)/judge/form.tsx";
const COPY = "lib/services/judge/copy.ts";

const C = {
  a01: "C3L-A01 the provider comes from the guarded workspace, never from the form; the call rides the session",
  a02: "C3L-A02 malformed org, model, run or key ids are refused before any call",
  a03: "C3L-A03 a well-formed foreign org or model is the server's refusal: denied, nothing else",
  a04: "C3L-A04 the route's refusals keep their meaning: 401/403/404 denied, 409 conflict, 422 invalid",
  a05: "C3L-A05 a failed or thrown call, or a misconfigured Lab, is unavailable, never denied and never data",
  a06: "C3L-A06 a workspace without run_evaluation has no judge action; only manage_members sets a budget",
  b01: "C3L-B01 a budget is PROVIDER_USD, exact to 1e-8, for this provider's own named payer, by PUT with the form's key",
  b02: "C3L-B02 a double-click submit is one run: both clicks carry the form's run id as the Idempotency-Key",
  b03: "C3L-B03 a configuration posts the typed body with numbers and the form's key",
  p01: "C3L-P01 calibration is the configuration's agreement as the route reports it, never a page of labels",
  guard: "L1-B01 every page, route, provider layout and server action calls the provider guard",
  r01: "J3L-R01 a developer reads this request's runs for the guarded workspace only, mapped to V3's live runs",
  r02: "J3L-R02 a viewer is denied and a misconfigured Lab is unavailable, before any call",
  r03: "J3L-R03 the route's refusals keep their meaning: 401/403/404 denied; an outage, a lost answer or a 409/422 unavailable",
  r04: "J3L-R04 an answer it cannot read whole is unavailable: never CREDIT as PROVIDER_USD, never a partial history",
  p1: "C3P-01 the page runs the guard, then mints one run id per render into the run form only",
  p2: "C3P-02 the four forms post the four C3L actions and never a provider or identity field",
  p3: "C3P-03 a viewer gets no judge form; only an administrator gets the budget form",
  p4: "C3P-04 every outcome has fixed copy, and a refusal never renders data",
};
const FORGED = "(input.provider_org_id as string) ?? w.providerId";
const PROVIDER = "query: { provider_org_id: w.providerId }";

const MUTANTS = [
  // AP-09 09c: the judge actions over /lab/v1/judge/* (replace C3L-X01..X36 and J3L-X01..X23, the RPC adapter's).
  m("C3L-X01", "configure takes the provider from the form", CORE, `${PROVIDER}, idempotencyKey: key,\n    body: { grantor_org_id`, `query: { provider_org_id: ${FORGED} }, idempotencyKey: key,\n    body: { grantor_org_id`, [C.a01]),
  m("C3L-X02", "the run request takes the provider from the form", CORE, `${PROVIDER}, idempotencyKey: run,`, `query: { provider_org_id: ${FORGED} }, idempotencyKey: run,`, [C.a01]),
  m("C3L-X03", "the budget takes the provider from the form", CORE, `params: { payer_ref: payer }, ${PROVIDER},`, `params: { payer_ref: payer }, query: { provider_org_id: ${FORGED} },`, [C.a01]),
  m("C3L-X04", "the calibration takes the provider from the form", CORE, "query: { provider_org_id: w.providerId, config_id: config }", `query: { provider_org_id: ${FORGED}, config_id: config }`, [C.a01]),
  m("C3L-X05", "a malformed grantor id is sent", CORE, "const grantor = id(input.grantor_org_id);", "const grantor = input.grantor_org_id as string;", [C.a02]),
  m("C3L-X06", "a malformed model id is sent", CORE, "const model = id(input.model_id);", "const model = input.model_id as string;", [C.a02]),
  m("C3L-X07", "ids match case-insensitively", SHAPES, "new RegExp(`^${UUID}$`)", "new RegExp(`^${UUID}$`, \"i\")", [C.a02]),
  m("C3L-X08", "any judge model name is sent", CORE, 'typeof input.judge_model === "string" && MODEL.test(input.judge_model)', 'typeof input.judge_model === "string"', [C.a02]),
  m("C3L-X09", "the sample size is unbounded", CORE, "count(input.sample_size, MAX_SAMPLES)", "count(input.sample_size, 999_999)", [C.a02]),
  m("C3L-X36", "the rubric version is unbounded", CORE, "count(input.rubric_version, MAX_RUBRIC)", "count(input.rubric_version, 999_999)", [C.a02]),
  m("C3L-X10", "zero counts as a count", CORE, "return n >= 1 && n <= max ? n : null;", "return n >= 0 && n <= max ? n : null;", [C.a02]),
  m("C3L-X11", "a count may be any number syntax", CORE, 'typeof v === "string" && /^[0-9]{1,6}$/.test(v) ? Number(v) : NaN', 'typeof v === "string" ? Number(v) : NaN', [C.a02]),
  m("C3L-X12", "a malformed run id is sent", CORE, "const run = id(input.run_id);", "const run = input.run_id as string;", [C.a02]),
  m("C3L-X13", "a malformed config id is sent", CORE, "const config = id(input.config_id);\n  const payer", "const config = input.config_id as string;\n  const payer", [C.a02]),
  m("C3L-X37", "a malformed calibration config id is sent", CORE, "const config = id(input.config_id);\n  if (!config) return INVALID;", "const config = input.config_id as string;\n  if (!config) return INVALID;", [C.a02]),
  m("C3L-X38", "a configuration without the form's key is sent", CORE, "|| samples === null || !key) return INVALID;", "|| samples === null) return INVALID;", [C.a02]),
  m("C3L-X14", "a refusal reads as unavailable", CORE, 'if ([401, 403, 404].includes(error.status)) return "denied";', 'if ([401, 403, 404].includes(error.status)) return "unavailable";', [C.a03, C.a04]),
  m("C3L-X15", "an outage reads as a refusal", CORE, 'error.status === 422 ? "invalid" : "unavailable";', 'error.status === 422 ? "invalid" : "denied";', [C.a05]),
  m("C3L-X16", "a lost answer reads as a refusal", CORE, 'if (error.kind === "unavailable") return "unavailable";', '', [C.a05]),
  m("C3L-X17", "a route error is data", CORE, "(r.ok ? { ok: true, data: r.data } : { ok: false, reason: reasonOf(r.error) })", "({ ok: true, data: r.ok ? r.data : null })", [C.a03, C.a04, C.a05]),
  m("C3L-X39", "a reused key with other values reads as invalid", CORE, 'return error.status === 409 ? "conflict" :', 'return error.status === 409 ? "invalid" :', [C.a04]),
  m("C3L-X40", "a rejected shape reads as unavailable", CORE, 'error.status === 422 ? "invalid" :', 'error.status === 422 ? "unavailable" :', [C.a04]),
  m("C3L-X41", "a misconfigured Lab reads as invalid", CORE, "  if (api === null) return UNAVAILABLE;\n  return answer(await api.call(\"post\", \"/lab/v1/judge/configs\"", "  if (api === null) return INVALID;\n  return answer(await api.call(\"post\", \"/lab/v1/judge/configs\"", [C.a05]),
  m("C3L-X18", "a viewer configures the judge", CORE, '  if (!holds(w, "run_evaluation")) return REFUSED;\n  const grantor', "  const grantor", [C.a06]),
  m("C3L-X19", "a viewer requests a run", CORE, '  if (!holds(w, "run_evaluation")) return REFUSED;\n  const run', "  const run", [C.a06]),
  m("C3L-X20", "a viewer reads calibration", CORE, '  if (!holds(w, "run_evaluation")) return REFUSED;\n  const config = id(input.config_id);\n  if', "  const config = id(input.config_id);\n  if", [C.a06]),
  m("C3L-X21", "a developer sets a budget", CORE, '!holds(w, "manage_members")', '!holds(w, "run_evaluation")', [C.a06]),
  m("C3L-X42", "the role's name decides, not the API's set", CORE, '  if (!holds(w, "run_evaluation")) return REFUSED;\n  const grantor', '  if (!holds(w.role, "run_evaluation")) return REFUSED;\n  const grantor', [C.a06]),
  m("C3L-X22", "another provider's payer pays", CORE, "PAYER.exec(v)?.[1] === w.providerId", "PAYER.test(v)", [C.b01]),
  m("C3L-X23", "any ref kind is a payer", CORE, "`^lab:payer:(${UUID})", "`^lab:[a-z_]+:(${UUID})", [C.b01]),
  m("C3L-X24", "the limit is not an exact Lab amount", CORE, 'typeof input.limit_usd === "string" && USD.test(input.limit_usd)', 'typeof input.limit_usd === "string"', [C.b01]),
  m("C3L-X25", "the budget is CREDIT", CORE, 'unit: "PROVIDER_USD"', 'unit: "CREDIT"', [C.b01]),
  m("C3L-X43", "a budget without the form's key is sent", CORE, "if (!payer || !limit || !key) return INVALID;", "if (!payer || !limit) return INVALID;", [C.b01]),
  m("C3L-X44", "the budget is sent as a POST", CORE, 'api.call("put", "/lab/v1/judge/budgets/{payer_ref}"', 'api.call("post", "/lab/v1/judge/budgets/{payer_ref}"', [C.b01]),
  m("C3L-X26", "each click mints its own run key", CORE, "idempotencyKey: run,", "idempotencyKey: crypto.randomUUID(),", [C.b02]),
  m("C3L-X45", "the configuration's numbers go as text", CORE, "rubric_version: rubric, sample_size: samples", "rubric_version: String(rubric), sample_size: String(samples)", [C.b03]),
  m("C3L-X46", "the configuration carries no key", CORE, "provider_org_id: w.providerId }, idempotencyKey: key,\n    body: { grantor_org_id", "provider_org_id: w.providerId },\n    body: { grantor_org_id", [C.b03]),
  m("C3L-X47", "the calibration names no configuration", CORE, "provider_org_id: w.providerId, config_id: config }", "provider_org_id: w.providerId }", [C.p01]),
  m("C3L-X35", "a judge action skips the provider guard", ACTIONS, "export async function configureJudge(formData: FormData): Promise<Outcome> {\n  const workspace = await requireProviderWorkspace();",
    'export async function configureJudge(formData: FormData): Promise<Outcome> {\n  const workspace = { providerId: "", providerName: "", role: "administrator" as const, capabilities: [] };', [C.guard]),
  // --- J3 / WR-V3-1, register row 98: the per-request judge read (runs.ts) over /lab/v1/traces/{id}/judge-runs ---
  m("J3L-X01", "a viewer reads judge results", RUNS, '      if (actor.role === "viewer") return { ok: false, reason: "denied" };\n', "", [C.r02]),
  m("J3L-X02", "a misconfigured Lab invents an empty history", RUNS, 'if (api === null) return { ok: false, reason: "unavailable" };', "if (api === null) return { ok: true, value: [] };", [C.r02]),
  m("J3L-X03", "the provider is not the guarded workspace", RUNS, "query: { provider_org_id: actor.providerId }", "query: { provider_org_id: requestId }", [C.r01]),
  m("J3L-X04", "a refusal reads as unavailable", RUNS, 'reasonOf(answer.error) === "denied" ? "denied" : "unavailable"', '"unavailable"', [C.r03]),
  m("J3L-X05", "an outage or a 409/422 reads as a refusal", RUNS, 'reasonOf(answer.error) === "denied" ? "denied" : "unavailable"', 'reasonOf(answer.error) === "unavailable" ? "unavailable" : "denied"', [C.r03]),
  m("J3L-X06", "a first page reads as the whole history", RUNS, '        if (page.next_cursor) return { ok: false, reason: "unavailable" };\n', "", [C.r04]),
  m("J3L-X07", "a cost in any unit reads as PROVIDER_USD", RUNS, '(v as Row).unit === "PROVIDER_USD" && ', "", [C.r04]),
  m("J3L-X08", "an inexact amount is shown", RUNS, "USD_RE.test((v as Row).amount as string)", 'typeof (v as Row).amount === "string"', [C.r04]),
  m("J3L-X09", "a run that sent the request reads as a dry run", RUNS, 'mode: "live",', 'mode: "dry_run",', [C.r01]),
  m("J3L-X10", "an unknown run state is shown", RUNS, "state: one(r.state, STATES),", 'state: r.state as JudgeRun["state"],', [C.r04]),
  m("J3L-X11", "a fractional score is shown", RUNS, "(Number.isInteger(v) ? (v as number) : bad())", '(typeof v === "number" ? v : bad())', [C.r04]),
  m("J3L-X12", "a criterion's media flag is assumed", RUNS, "requiresMedia: bool(s.requires_media)", "requiresMedia: false", [C.r01, C.r04]),
  m("J3L-X13", "an unknown calibration state is shown", RUNS, "state: one(c.state, CALIBRATION),", 'state: c.state as "calibrated",', [C.r04]),
  m("J3L-X14", "a text verdict is shown", RUNS, "overallPass: opt(r.overall_pass, bool),", "overallPass: (r.overall_pass ?? null) as boolean | null,", [C.r04]),
  m("J3L-X15", "an absent verdict is unreadable", RUNS, "(v === null || v === undefined ? null : read(v))", "(v === null ? null : read(v))", [C.r01]),
  m("J3L-X16", "the settled cost's unit is not checked", RUNS, "actualUsd: opt(r.actual, usd),", "actualUsd: opt(r.actual, (v) => (v as Row).amount as string),", [C.r04]),
  m("J3L-X17", "a broken row is dropped, the rest shown", RUNS, "value: page.data.map(run) }", "value: page.data.flatMap((r) => { try { return [run(r)]; } catch { return []; } }) }", [C.r04]),
  m("J3L-X18", "a text rubric version is coerced", RUNS, "rubricVersion: int(r.rubric_version),", "rubricVersion: Number(r.rubric_version),", [C.r04]),
  m("J3L-X19", "the stored calibration interval is dropped", RUNS, "      interval,\n", "      interval: null,\n", [C.r01]),
  // --- WR-C3L-2: the judge page ------------------------------------------------------------------
  m("C3P-X01", "the page skips the provider guard", PAGE, "  const workspace = await requireProviderWorkspace();\n", '  const workspace = { role: "administrator" as string };\n', [C.p1, C.guard]),
  m("C3P-X02", "the run form mints its own id", PAGE, "hidden={{ run_id: runId }}", "hidden={{ run_id: crypto.randomUUID() }}", [C.p1]),
  m("C3P-X03", "the browser mints a run id per submit", FORM, "{Object.entries(hidden).map(", "{Object.entries({ ...hidden, run_id: crypto.randomUUID() }).map(", [C.p1]),
  m("C3P-X04", "the run request carries no run id", PAGE, "        hidden={{ run_id: runId }}\n", "", [C.p1]),
  m("C3P-X05", "a form carries the provider", PAGE, '{ name: "grantor_org_id", label: "Grantor organization id" },', '{ name: "provider_org_id", label: "Provider" },\n          { name: "grantor_org_id", label: "Grantor organization id" },', [C.p2]),
  m("C3P-X06", "a form posts the wrong action", PAGE, "action={judgeCalibrationPage}", "action={requestJudgeRun}", [C.p2]),
  m("C3P-X07", "hidden values are dropped", FORM, '        <input type="hidden" key={name} name={name} value={value} />\n', "        null\n", [C.p2]),
  m("C3P-X08", "a viewer gets the forms", PAGE, '  if (workspace.role === "viewer") return <p>Your role cannot configure or run the judge.</p>;\n', "", [C.p3]),
  m("C3P-X09", "a developer gets the budget form", PAGE, '{workspace.role === "administrator" && (', '{workspace.role !== "viewer" && (', [C.p3]),
  m("C3P-X10", "a refusal reads as another refusal", COPY, 'denied: "Refused: this workspace may not do that.",', 'denied: "The judge service is unavailable. Nothing was changed.",', [C.p4]),
  m("C3P-X11", "a refusal renders the server's answer", COPY, "if (!outcome.ok) return REFUSED[outcome.reason];", "if (!outcome.ok) return JSON.stringify(outcome);", [C.p4]),
  m("C3P-X12", "the calibration's state is not reported", COPY, "return `Calibration: ${c.state}, ${c.labels} of ${c.required} reviewed labels.`;", 'return "Done.";', [C.p4]),
  m("C3P-X14", "an unknown calibration state is reported", COPY, "c && STATES.includes(c.state as string) && ", "c && ", [C.p4]),
  m("C3P-X15", "a non-integer label count is reported", COPY, " && Number.isInteger(c.labels)", "", [C.p4]),
  m("C3P-X16", "a conflict reads as another refusal", COPY, 'conflict: "Already submitted with different values. Reload the page and try again.",', 'conflict: "Check the fields: something is not in the expected form.",', [C.p4]),
  m("C3P-X13", "the form never shows the outcome", FORM, '{state && <p role="status">{outcomeText(state)}</p>}', "{null}", [C.p4]),
];

// The counted cases: C3L, J3L, C3P and L1's guard case L1-B01 (L1's other cases are its own runner's).
process.exit(await runMutants({ suite: SUITE, prefix: "(?:C3L|J3L|C3P|L1(?=-B01 ))", mutants: MUTANTS }));
