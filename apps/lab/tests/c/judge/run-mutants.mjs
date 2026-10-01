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
const PAGE = "app/(provider)/judge/page.tsx";
const FORM = "app/(provider)/judge/form.tsx";
const COPY = "lib/services/judge/copy.ts";

const C = {
  a01: "C3L-A01 the provider comes from the guarded workspace, never from the form",
  a02: "C3L-A02 malformed org, model or run ids are refused before any call",
  a03: "C3L-A03 a well-formed foreign org or model is the server's refusal: denied, nothing else",
  a04: "C3L-A04 an expired grant denies the configuration, the run and the calibration page",
  a05: "C3L-A05 a failed or thrown call is unavailable, never denied and never data",
  a06: "C3L-A06 a viewer has no judge action; only an administrator sets a budget",
  b01: "C3L-B01 a budget is PROVIDER_USD, exact to 1e-8, for this provider's own named payer",
  b02: "C3L-B02 a double-click submit is one run: both clicks carry the form's run id",
  p01: "C3L-P01 calibration pages are bounded: the limit is clamped, a bad cursor or limit is refused",
  p02: "C3L-P02 a full page carries the next cursor, a short page ends, an over-long page is refused",
  s01: "C3L-S01 the calls ride the user's own Lab session; a misconfigured Lab builds no client",
  guard: "L1-B01 every page, route, provider layout and server action calls the provider guard",
  r01: "J3L-R01 the read names the session's provider and the request, and returns the rows",
  r02: "J3L-R02 a viewer or a malformed request id makes no call",
  r03: "J3L-R03 the server's refusal is denied; any other failure is unavailable",
  r04: "J3L-R04 one malformed row fails the whole read closed",
  r05: "J3L-R05 a no-media pass or an unsupported 'calibrated' is never passed through",
  p1: "C3P-01 the page runs the guard, then mints one run id per render into the run form only",
  p2: "C3P-02 the four forms post the four C3L actions and never a provider or identity field",
  p3: "C3P-03 a viewer gets no judge form; only an administrator gets the budget form",
  p4: "C3P-04 every outcome has fixed copy, and a refusal never renders data",
};
const FORGED = "(input.provider_org_id as string) ?? w.providerId";

const MUTANTS = [
  m("C3L-X01", "configure takes the provider from the form", CORE, "p_provider_org_id: w.providerId, p_grantor_org_id", `p_provider_org_id: ${FORGED}, p_grantor_org_id`, [C.a01]),
  m("C3L-X02", "the run request takes the provider from the form", CORE, "p_provider_org_id: w.providerId, p_run_id", `p_provider_org_id: ${FORGED}, p_run_id`, [C.a01]),
  m("C3L-X03", "the budget takes the provider from the form", CORE, "p_provider_org_id: w.providerId, p_payer_ref", `p_provider_org_id: ${FORGED}, p_payer_ref`, [C.a01]),
  m("C3L-X04", "the calibration page takes the provider from the form", CORE, "p_provider_org_id: w.providerId, p_after", `p_provider_org_id: ${FORGED}, p_after`, [C.a01]),
  m("C3L-X05", "a malformed grantor id is sent", CORE, "const grantor = id(input.grantor_org_id);", "const grantor = input.grantor_org_id as string;", [C.a02]),
  m("C3L-X06", "a malformed model id is sent", CORE, "const model = id(input.model_id);", "const model = input.model_id as string;", [C.a02]),
  m("C3L-X07", "ids match case-insensitively", CORE, "const ID = new RegExp(`^${UUID}$`);", 'const ID = new RegExp(`^${UUID}$`, "i");', [C.a02]),
  m("C3L-X08", "any judge model name is sent", CORE, 'typeof input.judge_model === "string" && MODEL.test(input.judge_model)', 'typeof input.judge_model === "string"', [C.a02]),
  m("C3L-X09", "the sample size is unbounded", CORE, "count(input.sample_size, MAX_SAMPLES)", "count(input.sample_size, 999_999)", [C.a02]),
  m("C3L-X10", "zero counts as a count", CORE, "return n >= 1 && n <= max ? n : null;", "return n >= 0 && n <= max ? n : null;", [C.a02, C.p01]),
  m("C3L-X11", "a count may be any number syntax", CORE, 'typeof v === "string" && /^[0-9]{1,6}$/.test(v) ? Number(v) : NaN', 'typeof v === "string" ? Number(v) : NaN', [C.a02, C.p01]),
  m("C3L-X12", "a malformed run id is sent", CORE, "const run = id(input.run_id);", "const run = input.run_id as string;", [C.a02]),
  m("C3L-X13", "a malformed config id is sent", CORE, "const config = id(input.config_id);", "const config = input.config_id as string;", [C.a02]),
  m("C3L-X14", "a refusal reads as unavailable", CORE, "? REFUSED : UNAVAILABLE;", "? UNAVAILABLE : UNAVAILABLE;", [C.a03, C.a04]),
  m("C3L-X15", "any error reads as a refusal", CORE, "? REFUSED : UNAVAILABLE;", "? REFUSED : REFUSED;", [C.a05]),
  m("C3L-X16", "a thrown call reads as a refusal", CORE, "  } catch {\n    return UNAVAILABLE;", "  } catch {\n    return REFUSED;", [C.a05]),
  m("C3L-X17", "an RPC error is data", CORE, "    if (!error) return { ok: true, data };", "    return { ok: true, data };", [C.a03, C.a04, C.a05]),
  m("C3L-X18", "a viewer configures the judge", CORE, "  if (RANK[w.role] < RANK.developer) return REFUSED;\n  const grantor", "  const grantor", [C.a06]),
  m("C3L-X19", "a viewer requests a run", CORE, "  if (RANK[w.role] < RANK.developer) return REFUSED;\n  const run", "  const run", [C.a06]),
  m("C3L-X20", "a viewer reads calibration", CORE, "  if (RANK[w.role] < RANK.developer) return REFUSED;\n  const cursor", "  const cursor", [C.a06]),
  m("C3L-X21", "a developer sets a budget", CORE, "RANK[w.role] < RANK.administrator", "RANK[w.role] < RANK.developer", [C.a06]),
  m("C3L-X22", "another provider's payer pays", CORE, "PAYER.exec(v)?.[1] === w.providerId", "PAYER.test(v)", [C.b01]),
  m("C3L-X23", "any ref kind is a payer", CORE, "`^lab:payer:(${UUID})", "`^lab:[a-z_]+:(${UUID})", [C.b01]),
  m("C3L-X24", "the limit is not an exact Lab amount", CORE, 'typeof input.limit_usd === "string" && USD.test(input.limit_usd)', 'typeof input.limit_usd === "string"', [C.b01]),
  m("C3L-X25", "the budget is CREDIT", CORE, 'unit: "PROVIDER_USD"', 'unit: "CREDIT"', [C.b01]),
  m("C3L-X26", "each click mints its own run id", CORE, "p_run_id: run,", "p_run_id: crypto.randomUUID(),", [C.b02]),
  m("C3L-X27", "the page size is not clamped", CORE, "const size = Math.min(limit, PAGE_MAX);", "const size = limit;", [C.p01]),
  m("C3L-X28", "a malformed cursor is sent", CORE, '(cursor !== "" && !after) || limit === null', "limit === null", [C.p01]),
  m("C3L-X29", "an over-long server page is rendered", CORE, "!Array.isArray(out.data) || out.data.length > size", "!Array.isArray(out.data)", [C.p02]),
  m("C3L-X30", "a short page claims a next page", CORE, "rows.length === size ?", "rows.length > 0 ?", [C.p02]),
  m("C3L-X31", "the session client drops the Lab cookie options", CORE, "    cookieOptions: authCookieOptions(config),\n", "", [C.s01]),
  m("C3L-X32", "a misconfigured Lab still builds a client", CORE, '  if (config === null) return async () => ({ data: null, error: { code: "misconfigured" } });\n', '  if (config === null) return make("", "", {} as ClientOptions).rpc;\n', [C.s01]),
  m("C3L-X33", "a refreshed session cookie is dropped", CORE, "setAll: (list) => list.forEach(({ name, value, options }) => store.set(name, value, options)),", "setAll: () => {},", [C.s01]),
  m("C3L-X34", "the session client adds an identity to every call", CORE, "return (name, args) => client.rpc(name, args);", 'return (name, args) => client.rpc(name, { ...args, p_user_id: "" });', [C.s01]),
  m("C3L-X35", "a judge action skips the provider guard", ACTIONS, "export async function configureJudge(formData: FormData): Promise<Outcome> {\n  const workspace = await requireProviderWorkspace();",
    'export async function configureJudge(formData: FormData): Promise<Outcome> {\n  const workspace = { providerId: "", providerName: "", role: "administrator" as const };', [C.guard]),
  // --- J3 / WR-V3-1: the per-request judge read (runs.ts) ---------------------------------------
  m("J3L-X01", "the read takes the provider from the request", RUNS, "{ p_provider_org_id: actor.providerId,", "{ p_provider_org_id: requestId,", [C.r01]),
  m("J3L-X02", "the request id is not sent", RUNS, "p_request_id: requestId }", "p_request_id: null }", [C.r01]),
  m("J3L-X03", "a viewer reads judge runs", RUNS, '      if (actor.role === "viewer") return { ok: false, reason: "denied" };\n', "", [C.r02]),
  m("J3L-X04", "a malformed request id is sent", RUNS, 'if (typeof requestId !== "string" || !ID.test(requestId))', 'if (typeof requestId !== "string")', [C.r02]),
  m("J3L-X05", "ids match case-insensitively", RUNS, "[0-9a-f]{12}$/;", "[0-9a-f]{12}$/i;", [C.r02]),
  m("J3L-X06", "P0002 (a foreign or unknown row) is unavailable", RUNS, 'code === "42501" || code === "P0002" ?', 'code === "42501" ?', [C.r03]),
  m("J3L-X07", "any error reads as denied", RUNS, '? "denied" : "unavailable" };', '? "denied" : "denied" };', [C.r03]),
  m("J3L-X08", "a thrown call reads as denied", RUNS, '      } catch {\n        return { ok: false, reason: "unavailable" };', '      } catch {\n        return { ok: false, reason: "denied" };', [C.r03]),
  m("J3L-X09", "any mode is read", RUNS, "&& MODES.includes(r.mode as string)", "", [C.r04]),
  m("J3L-X10", "any state is read", RUNS, "    && STATES.includes(r.state as string) ", "    ", [C.r04]),
  m("J3L-X11", "an inexact USD hold is read", RUNS, 'typeof r.reservedUsd === "string" && USD.test(r.reservedUsd)', 'typeof r.reservedUsd === "string"', [C.r04]),
  m("J3L-X12", "a numeric actual USD is read", RUNS, '(r.actualUsd === null || (typeof r.actualUsd === "string" && USD.test(r.actualUsd)))', "true", [C.r04]),
  m("J3L-X13", "a fractional rubric version is read", RUNS, "int(r.rubricVersion, 1)", "typeof r.rubricVersion === \"number\"", [C.r04]),
  m("J3L-X14", "a non-boolean media flag is read", RUNS, '&& typeof r.media === "boolean" ', "", [C.r04]),
  m("J3L-X15", "a textual score is read", RUNS, "(x.score === null || int(x.score, 0))", "true", [C.r04]),
  m("J3L-X16", "a textual verdict is read", RUNS, '(r.overallPass === null || typeof r.overallPass === "boolean")', "true", [C.r04]),
  m("J3L-X17", "a negative label count is read", RUNS, "int(c.labels, 0)", "typeof c.labels === \"number\"", [C.r04]),
  m("J3L-X18", "an inverted interval is read", RUNS, " && (interval[0] as number) <= (interval[1] as number)", "", [C.r04]),
  m("J3L-X19", "a no-media pass is passed through (R56)", RUNS, "const blindPass = !run.media", "const blindPass = false && !run.media", [C.r05]),
  m("J3L-X20", "any no-media pass is refused, even a text-only rubric's", RUNS, "run.scores.some((s) => s.requiresMedia)", "true", [C.r05]),
  m("J3L-X21", "'calibrated' on too few labels is passed through", RUNS, "(cal.labels < cal.required || ", "(", [C.r05]),
  m("J3L-X22", "'calibrated' without an agreement is passed through", RUNS, "cal.agreement === null || cal.interval", "cal.interval", [C.r05]),
  m("J3L-X23", "'calibrated' without an interval is passed through", RUNS, "|| cal.interval === null)", ")", [C.r05]),
  // --- WR-C3L-2: the judge page ------------------------------------------------------------------
  m("C3L-X36", "a blank optional page size is refused", CORE, 'input.limit === undefined || input.limit === "" ?', "input.limit === undefined ?", [C.p01]),
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
  m("C3P-X12", "the next cursor is dropped", COPY, 'typeof page.next === "string" ?', "false ?", [C.p4]),
  m("C3P-X13", "the form never shows the outcome", FORM, '{state && <p role="status">{outcomeText(state)}</p>}', "{null}", [C.p4]),
];

// The counted cases: C3L, J3L, C3P and L1's guard case L1-B01 (L1's other cases are its own runner's).
process.exit(await runMutants({ suite: SUITE, prefix: "(?:C3L|J3L|C3P|L1(?=-B01 ))", mutants: MUTANTS }));
