#!/usr/bin/env node
// C3F's Lab mutant runner (R32; LANE-RULES addendum): every decision of lib/services/review/ is one edit
// that a case it names must fail by assertion (L1's boundary case guards the action's guard). A stale
// `find`, a load failure or a failure in an undeclared case is not a kill. Harness as tests/l/shell.
// Usage: node tests/c/review/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const base = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
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

const m = (id, what, file, find, replace, cases) => ({ id, what, file, find, replace, cases });
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
const all = [...baseline.out.matchAll(/^ *ok \d+ - (.*)$/gm)].map((x) => x[1].trim());
const cases = all.filter((name) => name.startsWith("C3F-")); // L1's own cases are L1's runner's
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !all.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
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
