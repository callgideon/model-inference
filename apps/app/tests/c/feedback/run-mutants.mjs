#!/usr/bin/env node
// C3F's App mutant runner (R32; LANE-RULES addendum): every decision of lib/services/feedback.ts is one
// edit that a C3F case it names must fail by assertion. A stale `find`, a load failure or a failure in
// an undeclared case is not a kill. Harness as tests/l/shell/run-mutants.mjs in the Lab.
// Usage: node tests/c/feedback/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const base = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const SUITE = ["tests/c/feedback/feedback.test.ts", "tests/c/feedback/compose.test.ts"];
const F = "lib/services/feedback.ts";
const ACTIONS = "lib/services/actions.ts";

const C = {
  a01: "C3F-A01 the action forwards only the signal, over the one named door, from a ready session",
  a02: "C3F-A02 a smuggled provenance field is invalid_request and never reaches the door",
  a03: "C3F-A03 only a ready session submits; nothing else reaches the door",
  a04: "C3F-A04 the door's refusals keep their code; its text never reaches the caller",
  a05: "C3F-A05 flag off, a missing door, a denied role and a lost answer are never a success",
  a06: "C3F-A06 an acknowledgment is only a stored customer console signal; any other provenance fails closed",
  c01: "C3F-C01 the composed action refuses a cross-site request before resolving anyone",
  c02: "C3F-C02 an acknowledged signal refreshes the traces; a refusal or no client refreshes nothing",
};

const m = (id, what, find, replace, cases) => ({ id, what, file: F, find, replace, cases });
const MUTANTS = [
  { id: "C3F-CX01", what: "the composed action skips the Origin check", file: ACTIONS, find: "    submitFeedback: (input: FeedbackInput) =>\n      guarded(async () => {", replace: "    submitFeedback: (input: FeedbackInput) =>\n      (async (run: () => Promise<Result<FeedbackEntry>>, _p: string) => run())(async () => {", cases: [C.c01] },
  { id: "C3F-CX02", what: "the traces are not refreshed after an acknowledgment", file: ACTIONS, find: '      }, "/traces"),', replace: "      }),", cases: [C.c02] },
  { id: "C3F-CX03", what: "no client passes as a success", file: ACTIONS, find: 'if (deps.feedback === undefined) return fail<FeedbackEntry>("dependency_unavailable"', replace: 'if (deps.feedback === undefined) return fail<FeedbackEntry>("not_found"', cases: [C.c02] },
  m("C3F-AX01", "a smuggled field is passed on", "  if (rejected !== null) return rejected;\n", "", [C.a02]),
  m("C3F-AX02", "the door name drifts", '"submit_feedback"', '"submit_own_feedback"', [C.a01]),
  m("C3F-AX03", "the caller's input goes to the door verbatim", "{ p_args: { request_id, name, value, comment, idempotency_key } }", "{ p_args: { ...(input as object), author_role: \"customer\" } }", [C.a01]),
  m("C3F-AX04", "a session that is not ready submits", '  if (context.state !== "ready") return fail("forbidden", "sign in with a verified account first");\n', "", [C.a03]),
  m("C3F-AX05", "an unchecked account reads as forbidden", 'if (context.state === "unavailable") return fail("dependency_unavailable"', 'if (context.state === "unavailable") return fail("forbidden"', [C.a03]),
  m("C3F-AX06", "the database's text reaches the caller", "fail(code as ErrorCode, message)", "fail(code as ErrorCode, error.message ?? message)", [C.a04]),
  m("C3F-AX07", "a refusal loses its code", "fail(code as ErrorCode, message)", "fail(\"dependency_unavailable\", message)", [C.a04]),
  m("C3F-AX08", "an unknown refusal passes through as its own code", "return message === undefined ? fail(\"dependency_unavailable\", UNKNOWN)", "return message === undefined ? fail((code ?? \"dependency_unavailable\") as ErrorCode, UNKNOWN)", [C.a05]),
  m("C3F-AX09", "a denied role reads as unavailable", '  if (error.code === "42501") return fail("forbidden", "this account cannot send feedback");\n', "", [C.a05]),
  m("C3F-AX10", "a lost answer reads as not_found", "  } catch {\n    return fail(\"dependency_unavailable\", UNKNOWN);", "  } catch {\n    return fail(\"not_found\", UNKNOWN);", [C.a05]),
  m("C3F-AX11", "an operator-authored row is acknowledged", 'r.author_role !== "customer" || ', "", [C.a06]),
  m("C3F-AX12", "an api-channel row is acknowledged", 'r.channel !== "console" || ', "", [C.a06]),
  m("C3F-AX13", "a calibration row is acknowledged", 'r.calibration_set !== false || ', "", [C.a06]),
  m("C3F-AX14", "a rubric-versioned row is acknowledged", ' || r.rubric_version !== null) return null;', ") return null;", [C.a06]),
  m("C3F-AX15", "a label-named row is acknowledged", ' || r.name === "calibration_label") return null;', ") return null;", [C.a06]),
  m("C3F-AX16", "a row without an id is acknowledged", 'if (typeof r.feedback_id !== "string" || ', "if (", [C.a06]),
  m("C3F-AX17", "the author is not the stored one", "author_principal: r.author_principal as string,", "author_principal: \"platform\",", [C.a01]),
  m("C3F-AX18", "an unconfirmed write reads as a success", 'return entry === null ? fail("internal_error", UNKNOWN) : { ok: true, value: entry };', "return { ok: true, value: entry as FeedbackEntry };", [C.a06]),
];

function copy() {
  const root = mkdtempSync(join(tmpdir(), "c3f-mutants-"));
  cpSync(base, root, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(base.length)) });
  symlinkSync(join(base, "node_modules"), join(root, "node_modules"), "dir");
  return root;
}

function run(cwd) {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--test", "--test-reporter=tap", ...SUITE], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.stderr.on("data", (c) => (out += c));
    child.on("close", (code) => done({ code, out }));
  });
}

function failed(out) {
  const cases = [];
  const re = /^ *not ok \d+ - (.*)$/gm;
  for (let hit = re.exec(out); hit !== null; hit = re.exec(out)) {
    const rest = out.slice(hit.index + hit[0].length);
    const end = rest.search(/^ *\.\.\.$/m);
    cases.push({ name: hit[1].trim(), assertion: /code: 'ERR_ASSERTION'/.test(end === -1 ? rest : rest.slice(0, end)) });
  }
  return cases;
}

async function judge(mutant) {
  const pristine = readFileSync(join(base, mutant.file), "utf8");
  const hits = pristine.split(mutant.find).length - 1;
  if (hits !== 1) return `STALE (find matches ${hits} times)`;
  const root = copy();
  try {
    writeFileSync(join(root, mutant.file), pristine.replace(mutant.find, mutant.replace));
    const { code, out } = await run(root);
    if (code === 0) return "SURVIVED (suite passed)";
    const fails = failed(out);
    if (fails.some((f) => /\.test\.ts$/.test(f.name))) return "RUNNER-ERROR (a test file did not load)";
    const hit = fails.find((f) => f.assertion && mutant.cases.includes(f.name));
    return hit ? `killed by "${hit.name}"` : `SURVIVED (failed only: ${fails.map((f) => f.name).join("; ")})`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

// Every C3F case in the suite is named by at least one mutant, and every named case exists.
const declared = new Set(MUTANTS.flatMap((x) => x.cases));
const baseline = await run(base);
const cases = [...baseline.out.matchAll(/^ *ok \d+ - (C3F-\S+ .*)$/gm)].map((x) => x[1].trim());
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !cases.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
];
const selected = only === null ? MUTANTS : MUTANTS.filter((x) => only.includes(x.id));
if (selected.length === 0) problems.push("--only matched no mutant");
for (const problem of problems) console.log(`FAIL ${problem}`);
let survivors = 0;
for (const mutant of selected) {
  const verdict = await judge(mutant);
  if (!verdict.startsWith("killed")) survivors += 1;
  console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
}
console.log(`\n${cases.length} cases, all named: ${problems.length === 0}; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(problems.length === 0 && survivors === 0 ? 0 : 1);
