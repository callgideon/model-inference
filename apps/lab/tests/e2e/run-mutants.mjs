#!/usr/bin/env node
// LAB-E2E's mutant runner (R32; LANE-RULES addendum), two lists on the shared Lab harness' judging:
//  * the harness list (always; lab-mutants): tests/e2e/harness.test.ts over harness.ts;
//  * the stack list (LAB_E2E_REAL=1, Docker, the l4 key): each mutant is one edit to the Lab app a stack
//    suite drives, judged by running THAT suite against the mutated copy's own build (a copy builds with
//    webpack: its node_modules is a link) over its l4 backend; a named case must fail by assertion.
// Usage: node tests/e2e/run-mutants.mjs [--only ID,ID]
//        LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { failed, m, mutate, runMutants } from "../l/shell/harness.mjs";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
process.env.INFRX_API_DIR ??= resolve(lab, "../infrx-api"); // the copies are apps/lab only
const HARNESS = "tests/e2e/harness.ts";
const H = {
  h01: "E2E-H01 the page's text is what a reader sees: no script or style, React's separators dropped, entities decoded",
  h02: "E2E-H02 a form submits what a browser would: hidden action fields, the selected option, the textarea, never an unticked box",
  h03: "E2E-H03 a record's section is found by its own label only",
  h04: "E2E-H04 the browser keeps the cookies it is given, drops a cleared one, and posts a form as multipart from its own origin, unfollowed",
};
const HARNESS_MUTANTS = [
  m("E2E-X01", "script text is read as page text", HARNESS, '.replace(/<(script|style|template)\\b[\\s\\S]*?<\\/\\1>/gi, " ")', "", [H.h01]),
  m("E2E-X02", "React's text separators split a rendered value", HARNESS, '.replace(/<!--[\\s\\S]*?-->/g, "")', "", [H.h01]),
  m("E2E-X03", "entities are not decoded", HARNESS, 'return decode(body.replace(/<[^>]+>/g, " "))', 'return (body.replace(/<[^>]+>/g, " "))', [H.h01]),
  m("E2E-X04", "a hex entity is read as decimal", HARNESS, "parseInt(e.slice(2), 16)", "parseInt(e.slice(2), 10)", [H.h01]),
  m("E2E-X05", "an unticked box is submitted", HARNESS, '      if (type === "checkbox" || type === "radio") continue;\n', "", [H.h02]),
  m("E2E-X06", "the selected option is ignored", HARNESS, 'options.find((o) => /\\sselected(=""|\\s|>)/.test(o)) ?? options[0]', "options[0]", [H.h02]),
  m("E2E-X07", "a textarea is not submitted", HARNESS, "if (name !== null) (names.push(name), fields.push([name, decode(body)]));", "if (name !== null) names.push(name);", [H.h02]),
  m("E2E-X08", "a hidden field without a value is dropped", HARNESS, 'fields.push([name, attr(tag, "value") ?? ""]);', 'if (attr(tag, "value") !== null) fields.push([name, attr(tag, "value")!]);', [H.h02]),
  m("E2E-X09", "an ambiguous form match takes the first", HARNESS, "assert.equal(found.length, 1, `one form matching", "assert.ok(found.length >= 1, `one form matching", [H.h02]),
  m("E2E-X10", "a section runs on into the next record", HARNESS, 'html.slice(open, html.indexOf("</section>", open))', "html.slice(open)", [H.h03]),
  m("E2E-X11", "a missing section is the whole page", HARNESS, "return open === -1 ? null :", "return open === -1 ? html :", [H.h03]),
  m("E2E-X12", "a cleared cookie is kept", HARNESS, 'if (value === "" || /;\\s*max-age=0\\b/i.test(line)', "if (false", [H.h04]),
  m("E2E-X13", "an override is sent beside the form's own value", HARNESS, "if (!(name in values)) body.append(name, value);", "body.append(name, value);", [H.h04]),
  m("E2E-X14", "a form is posted without its origin (Next refuses a cross-origin action)", HARNESS, "headers: this.headers({ origin: this.base, accept: \"text/html\" }),", "headers: this.headers({ accept: \"text/html\" }),", [H.h04]),
  m("E2E-X15", "a redirect is followed, hiding where an action landed", HARNESS, 'method: "POST", body, redirect: "manual",', 'method: "POST", body, redirect: "follow",', [H.h04]),
  m("E2E-X16", "a value for a field the form does not have is sent anyway", HARNESS, "for (const name of Object.keys(values)) assert.ok(f.names.includes(name), `the form has no field ${name}: ${f.names.join(\", \")}`);\n", "", [H.h04]),
  m("E2E-X17", "the session cookie is not sent back", HARNESS, 'return { cookie: [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; "), ...extra };', "return { ...extra };", [H.h04]),
];

// ------------------------------------------------------------------ the stack list
const S = {
  O: "E2E-O o10 the Lab review panel renders the provider trace route's answer",
  o01: "E2E-O01 a developer's list shows its own deployment's requests with each one's content state, and nothing else",
  o02: "E2E-O02 the review page shows the record, then each panel's own answer: content on request, the feedback door, the judge door",
  o03: "E2E-O03 a viewer, another provider, a consumer-only account and a signed-out visitor see no request",
  o04: "E2E-O04 the grantor revokes: the same request reads as metadata only, with no organization or content offer",
  r01: "E2E-R01 as the gateway composes LAB_RELEASES today, the page fails closed: no rows, no form, no success",
  r02: "E2E-R02 a guardrail breach is one D9 rollback the page shows from the records, with nothing left to propose",
  r03: "E2E-R03 an expand verdict → the administrator proposes at the shown fence → the operator approves → approved, with its evidence",
  r04: "E2E-R04 an emergency rollback: the administrator proposes it, the operator approves it, the release rolls back once",
  r05: "E2E-R05 unsafe proposals are refused with fixed copy: a viewer, another provider, a stale fence, a double click; a rejection changes nothing",
  e01: "E2E-E01 as the gateway composes LAB_EVALS today, the page fails closed: nothing listed, nothing to launch",
  e02: "E2E-E02 a launch through the page's form lands on the experiment: its declared protocol and two queued runs",
  e03: "E2E-E03 progress and a cancel come from the records: a running run is cancelled once, a finished run offers none",
  e04: "E2E-E04 the comparison is B2's stored report as the view reads it: outcome, reasons, every estimate with its interval",
  e05: "E2E-E05 a viewer, another provider and a consumer-only account see and launch nothing that is not theirs",
  i01: "E2E-I01 as the gateway composes LAB_PIPELINES today: the annotations page reads, the training page fails closed",
  i02: "E2E-I02 labels imported through the page's form: accepted rows listed as not ground truth, rejected rows with their reason",
  i03: "E2E-I03 an administrator assigns the reviewer, the developer accepts each label: an accepted import is still not ground truth",
  i04: "E2E-I04 a train-only export through the page's form: train samples in its lineage, the held-out label omitted with its reason",
  i05: "E2E-I05 the manual bundle: prepared, submitted and finished from the training page; a returned checkpoint is never eligible without its held-out evaluation",
  i06: "E2E-I06 a viewer, another provider and a consumer-only account neither see nor write this provider's labels",
};
const LIST = "components/traces/list/view-model.ts";
const DETAIL = "components/traces/detail/view.ts";
const ACCESS = "lib/auth/access.ts";
const EMPTY = "{ ok: true, value: [] as unknown as T }";
const stackMutant = (suite, ...args) => ({ suite, ...m(...args) });
const STACK_MUTANTS = [
  stackMutant("observe", "E2E-S01", "a lost capture loses its reason in the list", LIST, "content: state === \"lost\" ? `${CONTENT_LABEL.lost}: ${reason}` : CONTENT_LABEL[state],", "content: CONTENT_LABEL[state],", [S.o01]),
  stackMutant("observe", "E2E-S02", "an ungranted request reads as expired content", DETAIL, '  if (d.access === "metadata") return "metadata_only";\n', "", [S.o01, S.o04]),
  stackMutant("observe", "E2E-S03", "content is loaded without being asked for", "app/(provider)/requests/[id]/page.tsx", 'const wanted = (await searchParams).content === "1";', "const wanted = true;", [S.o02]),
  stackMutant("observe", "E2E-S04", "a refused list reads as not found, whatever the reason", LIST, 'message: result.reason === "not_found" ? LIST_COPY.not_found : TRACE_COPY[result.reason]', "message: LIST_COPY.not_found", [S.o03]),
  stackMutant("observe", "E2E-S05", "a consumer-only account gets a workspace chooser", ACCESS, '  if (workspaces.length === 0) return { kind: "denied" };\n', "", [S.o03]),
  stackMutant("observe", "E2E-S06", "a metadata-only record shows an organization", DETAIL, 'if (d.access === "content") rows.push(["Organization", d.grantor_org_id]', 'if (true) rows.push(["Organization", (d as { grantor_org_id?: string }).grantor_org_id ?? ""]', [S.o04]),
  stackMutant("rollout", "E2E-S07", "an unavailable releases service reads as an empty list", "lib/services/rollouts/http.ts", 'if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };', `if (!response.ok) return ${EMPTY};`, [S.r01]),
  stackMutant("rollout", "E2E-S08", "a rolled-back release still offers a rollback", "lib/services/rollouts/view.ts", 'if (r.state !== "rolled_back") actions.push("rollback");', 'actions.push("rollback");', [S.r02]),
  stackMutant("rollout", "E2E-S09", "a decided proposal still reads as pending", "lib/services/rollouts/view.ts", 'x.policyRef === r.policyRef && x.state === "proposed"', "x.policyRef === r.policyRef", [S.r03, S.r05]),
  stackMutant("rollout", "E2E-S10", "an inconclusive verdict offers an expansion", "lib/services/rollouts/view.ts", 'r.state === "running" && v?.action === "expand"', 'r.state === "running"', [S.r04]),
  stackMutant("rollout", "E2E-S11", "a viewer is offered the proposal forms", "lib/services/rollouts/view.ts", 'if (!pending && holds(role, "propose_publication")) {', "if (!pending) {", [S.r05]),
  stackMutant("rollout", "E2E-S12", "the form proposes at another fence than the page showed", "lib/services/rollouts/actions.ts", "policyRef as string, Number(fence))", "policyRef as string, Number(fence) + 1)", [S.r03, S.r04, S.r05]),
  stackMutant("evaluate", "E2E-S13", "an unavailable evaluation service reads as empty lists", "lib/services/evaluation/http.ts", 'if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };', `if (!response.ok) return ${EMPTY};`, [S.e01]),
  stackMutant("evaluate", "E2E-S14", "a launch does not land on its experiment", "lib/services/evaluation/actions.ts", "(e) => `/experiments/${e.experiment_id}`", '() => "/evaluations"', [S.e02]),
  stackMutant("evaluate", "E2E-S15", "a finished run offers a cancel", "lib/services/evaluation/view.ts", '(run.state === "queued" || run.state === "running") && holds(role, "run_evaluation")', 'holds(role, "run_evaluation")', [S.e03]),
  stackMutant("evaluate", "E2E-S16", "a stored report is shown as pending", "app/(provider)/experiments/[id]/page.tsx", "const c = e.report === null ? null : comparison(e.report);", "const c = null as ReturnType<typeof comparison> | null;", [S.e04]),
  stackMutant("evaluate", "E2E-S17", "a viewer is offered the launch form", "app/(provider)/evaluations/page.tsx", '{holds(workspace.role, "run_evaluation") && (', "{true && (", [S.e05]),
  stackMutant("improve", "E2E-S18", "an unavailable pipeline service reads as empty lists", "lib/services/pipelines/http.ts", 'if (!response.ok) return { ok: false, reason: REASONS[response.status] ?? "unavailable" };', `if (!response.ok) return ${EMPTY};`, [S.i01]),
  stackMutant("improve", "E2E-S19", "a rejected import row loses its reason", "lib/services/pipelines/view.ts", "? IMPORT_COPY[x.reason] :", "? x.reason :", [S.i02]),
  stackMutant("improve", "E2E-S20", "an accepted import reads as ground truth", "lib/services/pipelines/view.ts", 'truth: l.method === "human" && l.groundTruth ?', 'truth: l.state === "accepted" ?', [S.i03]),
  stackMutant("improve", "E2E-S21", "an omitted label loses its reason", "lib/services/pipelines/view.ts", "omitted: e.omitted.map((x) => `${x.sampleId}: ${x.reason}`),", "omitted: e.omitted.map((x) => x.sampleId),", [S.i04]),
  stackMutant("improve", "E2E-S22", "a submitted manual run cannot be marked finished", "lib/services/pipelines/view.ts", '      if (r.state === "submitted" && manual(r)) actions.push("finish");\n', "", [S.i05]),
  stackMutant("improve", "E2E-S23", "a checkpoint without its held-out evaluation reads as eligible", "lib/services/pipelines/view.ts", ': !onHoldout ? "Not evaluated on this run\'s pinned holdout: not eligible."', ': !onHoldout ? "Eligible candidate: not public and not promoted."', [S.i05]),
  stackMutant("improve", "E2E-S24", "a viewer is offered the label import and export forms", "app/(provider)/annotations/page.tsx", '              {holds(workspace.role, "run_evaluation") && (\n                <>', "              {true && (\n                <>", [S.i06]),
];

function suiteRun(cwd, suite) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", `tests/e2e/${suite}/stack.test.ts`], {
      cwd, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, LAB_E2E_REAL: "1", INFRX_D_TASK: "l4", LAB_E2E_BUILT: "", LAB_E2E_OUT: "" },
    });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    child.on("close", (code) => done({ code, out }));
  });
}

async function judge(mutant) {
  const pristine = readFileSync(join(lab, mutant.file), "utf8");
  const hits = pristine.split(mutant.find).length - 1;
  if (hits !== 1) return `STALE (find matches ${hits} times)`;
  const root = mkdtempSync(join(tmpdir(), "lab-e2e-mutants-"));
  try {
    cpSync(lab, root, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(lab.length)) });
    symlinkSync(join(lab, "node_modules"), join(root, "node_modules"), "dir");
    writeFileSync(join(root, mutant.file), mutate(pristine, mutant.find, mutant.replace));
    const { code, out } = await suiteRun(root, mutant.suite);
    if (code === 0) return "SURVIVED (suite passed)";
    const hit = failed(out).find((f) => f.assertion && mutant.cases.includes(f.name));
    return hit ? `killed by "${hit.name}"` : `SURVIVED (failed only: ${failed(out).map((f) => f.name).join("; ") || out.slice(-400)})`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

async function stackList(only) {
  const declared = new Set(STACK_MUTANTS.flatMap((x) => x.cases));
  const problems = [];
  for (const suite of ["observe", "rollout", "evaluate", "improve"]) {
    const base = await suiteRun(lab, suite);
    if (base.code !== 0) problems.push(`the unmutated ${suite} suite does not pass`);
    const cases = [...base.out.matchAll(/^ *ok \d+ - (E2E-[OREI]\d\d .*)$/gm)].map((x) => x[1].trim());
    problems.push(...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`));
  }
  const known = new Set(Object.values(S).filter((n) => /^E2E-[OREI]\d\d /.test(n)));
  problems.push(...[...declared].filter((name) => !known.has(name)).map((name) => `a mutant names a missing case "${name}"`));
  const selected = only === null ? STACK_MUTANTS : STACK_MUTANTS.filter((x) => only.includes(x.id));
  for (const problem of problems) console.log(`FAIL ${problem}`);
  let survivors = 0;
  for (const mutant of selected) {
    const verdict = await judge(mutant);
    if (!verdict.startsWith("killed")) survivors += 1;
    console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
  }
  console.log(`\nstack: ${declared.size} cases named; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
  return problems.length === 0 && survivors === 0 ? 0 : 1;
}

const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const wants = (list) => only === null || list.some((x) => only.includes(x.id));
const harness = wants(HARNESS_MUTANTS) ? await runMutants({ suite: ["tests/e2e/harness.test.ts"], prefix: "E2E", mutants: HARNESS_MUTANTS }) : 0;
if (!wants(STACK_MUTANTS)) process.exit(harness);
if (process.env.LAB_E2E_REAL !== "1") {
  console.log("stack list: not run - LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs (Docker, the l4 key)");
  process.exit(harness);
}
process.exit((await stackList(only)) || harness);
