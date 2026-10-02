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
const F = "lib/services/actions.ts";

const C = {
  a01: "C3F-A01 the action forwards only the signal, as one feedback POST on the request, with the submission's key",
  a02: "C3F-A02 a smuggled provenance field is invalid_request and never reaches the API",
  a03: "C3F-A03 a malformed request id or a missing idempotency key never reaches the API",
  a04: "C3F-A04 the API's refusals keep their code; its text never reaches the caller",
  a05: "C3F-A05 the feature off, a dead session and a lost answer are never a success",
  a06: "C3F-A06 an acknowledgment is only this request's console signal; anything else fails closed",
  c01: "C3F-C01 the composed action refuses a cross-site request before resolving anyone",
  c02: "C3F-C02 a same-site signal reaches the API once as the session and refreshes nothing",
};

const m = (id, what, find, replace, cases) => ({ id, what, file: F, find, replace, cases });
const MUTANTS = [
  m("C3F-CX01", "the composed action skips the Origin check", "    submitFeedback: (input: FeedbackInput) => guarded(async () => submitFeedback(await deps.api(), input)),", "    submitFeedback: async (input: FeedbackInput) => submitFeedback(await deps.api(), input),", [C.c01]),
  m("C3F-CX02", "the composed action refreshes a page after feedback", "    submitFeedback: (input: FeedbackInput) => guarded(async () => submitFeedback(await deps.api(), input)),", "    submitFeedback: (input: FeedbackInput) => guarded(async () => submitFeedback(await deps.api(), input), \"/usage\"),", [C.c02]),
  m("C3F-AX01", "a smuggled field is passed on", "  const rejected = badInput<FeedbackAck>(input, FEEDBACK_INPUT_FIELDS);\n  if (rejected !== null) return rejected;\n", "  const rejected = null;\n", [C.a02]),
  m("C3F-AX02", "the route drifts", '"/console/v1/requests/{request_id}/feedback"', '"/console/v1/requests/{request_id}/result" as "/console/v1/requests/{request_id}/feedback"', [C.a01]),
  m("C3F-AX03", "the caller's input goes to the API verbatim", "      body: { name, value: value as boolean | number | string, comment: typeof comment === \"string\" ? comment : null },", "      body: input as never,", [C.a01]),
  m("C3F-AX04", "the idempotency key is not sent", "      idempotencyKey: idempotency_key,\n", "", [C.a01]),
  m("C3F-AX05", "a malformed request id reaches the API", '  if (typeof request_id !== "string" || !UUID.test(request_id)) return fail("not_found", FEEDBACK_REFUSALS.not_found!);\n', "", [C.a03]),
  m("C3F-AX06", "a missing idempotency key reaches the API", '  if (typeof idempotency_key !== "string" || idempotency_key === "" || idempotency_key.length > MAX_IDEMPOTENCY_KEY_CHARS) {\n    return fail("invalid_request", "an idempotency key is required");\n  }\n  if (typeof name', "  if (typeof name", [C.a03]),
  m("C3F-AX07", "a non-scalar value reaches the API", ' || !["boolean", "number", "string"].includes(typeof value)) return fail("invalid_request", FEEDBACK_REFUSALS.invalid_request!);', ') return fail("invalid_request", FEEDBACK_REFUSALS.invalid_request!);', [C.a03]),
  m("C3F-AX08", "the API's text reaches the caller", "  return message === undefined ? fail(\"dependency_unavailable\", unknown) : fail(code as ErrorCode, message);", "  return message === undefined ? fail(\"dependency_unavailable\", unknown) : fail(code as ErrorCode, error.kind === \"error\" ? error.message : message);", [C.a04]),
  m("C3F-AX09", "an unknown refusal passes through as its own code", "  return message === undefined ? fail(\"dependency_unavailable\", unknown) : fail(code as ErrorCode, message);", "  return message === undefined ? fail((code ?? \"dependency_unavailable\") as ErrorCode, unknown) : fail(code as ErrorCode, message);", [C.a05]),
  m("C3F-AX10", "a dead session reads as unavailable", '  if (error.status === 401) return fail("forbidden", "your session has ended; sign in again");\n', "", [C.a05]),
  m("C3F-AX11", "an unreadable acknowledgment reads as not now", "    return fail(\"internal_error\", FEEDBACK_UNKNOWN);\n  }\n}", "    return fail(\"dependency_unavailable\", FEEDBACK_UNKNOWN);\n  }\n}", [C.a06]),
  m("C3F-AX12", "an api-channel acknowledgment is accepted", 'ack.channel !== "console" || ', "", [C.a06]),
  m("C3F-AX13", "another request's acknowledgment is accepted", ' || ack.request_id !== request_id) return fail("internal_error", FEEDBACK_UNKNOWN);', ') return fail("internal_error", FEEDBACK_UNKNOWN);', [C.a06]),
  m("C3F-AX14", "a replay is reported as new", "replayed: ack.replayed === true", "replayed: false", [C.a06]),
  m("C3F-AX15", "the acknowledgment names the caller's id, not the stored one", "  return ok({ id: ack.feedback_id,", "  return ok({ id: idempotency_key,", [C.a01]),
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
  const re = /^( *)not ok \d+ - (.*)$/gm;
  for (let hit = re.exec(out); hit !== null; hit = re.exec(out)) {
    const rest = out.slice(hit.index + hit[0].length);
    // The block ends at `...` indented two past the `not ok`; a long diff elides lines with a bare `...` too.
    const end = rest.search(new RegExp(`^ {${hit[1].length + 2}}\\.\\.\\.$`, "m"));
    cases.push({ name: hit[2].trim(), assertion: /code: 'ERR_ASSERTION'/.test(end === -1 ? rest : rest.slice(0, end)) });
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
