#!/usr/bin/env node
// C2's Lab mutant runner (R32; LANE-RULES addendum): every decision of the content-ref adapter is one
// edit that a case it names must fail by assertion. Judging is tests/l/shell/run-mutants.mjs's.
// Usage: node tests/c/content/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const SUITE = ["tests/c/content/refs.test.ts"];
const R = "lib/services/content/refs.ts";

const C = {
  l01: "C2-L01 the issue is the named RPC with the handle's digest only, never the handle or an identity",
  l02: "C2-L02 a refusal maps by its code; anything else, or a throw, is unavailable",
  l03: "C2-L03 a malformed answer fails closed",
  l04: "C2-L04 a malformed input never reaches the RPC",
  l05: "C2-L05 every content state reads as a sentence; an unknown one reads as lost",
};

const m = (id, what, file, find, replace, cases) => ({ id, what, file, find, replace, cases });
const MUTANTS = [
  m("C2-X01", "the raw handle is sent", R, "p_handle_sha256: handleDigest(handle),", "p_handle_sha256: handle,", [C.l01]),
  m("C2-X02", "a fixed handle", R, 'return "tc_" + randomBytes(32).toString("base64url");', 'return "tc_" + "a".repeat(43);', [C.l01]),
  m("C2-X03", "an identity is sent", R, "      p_purpose: input.purpose,\n", "      p_purpose: input.purpose,\n      p_user_id: input.providerOrgId,\n", [C.l01]),
  m("C2-X04", "the digest is not SHA-256", R, 'createHash("sha256")', 'createHash("sha1")', [C.l01]),
  m("C2-X05", "an expiry reads as not found", R, "result_expired: \"expired\"", "result_expired: \"not_found\"", [C.l02]),
  m("C2-X06", "an unknown code is taken as a refusal", R, 'Object.hasOwn(REFUSALS, code) ? REFUSALS[code] : "unavailable"', '(REFUSALS[code] ?? "unavailable")', [C.l02]),
  m("C2-X07", "a thrown transport escapes", R, "  } catch {\n    return { ok: false, reason: \"unavailable\" };\n  }\n}", "  } finally {\n  }\n}", [C.l02]),
  m("C2-X08", "any grantor is accepted", R, 'if (typeof grantor !== "string" || !UUID.test(grantor))', 'if (typeof grantor !== "string")', [C.l03]),
  m("C2-X09", "another request's answer is accepted", R, "if (request !== input.requestId || ", "if (typeof request !== \"string\" || ", [C.l03]),
  m("C2-X10", "an unparseable expiry is accepted", R, ' || Number.isNaN(Date.parse(expires))', "", [C.l03]),
  m("C2-X11", "a malformed id reaches the RPC", R, "  if (!UUID.test(input.providerOrgId) || !UUID.test(input.requestId)) return { ok: false, reason: \"not_found\" };\n", "", [C.l04]),
  m("C2-X12", "any purpose reaches the RPC", R, '  if (!PURPOSES.includes(input.purpose)) return { ok: false, reason: "forbidden" };\n', "", [C.l04]),
  m("C2-X13", "an unknown state reads as nothing", R, "Object.hasOwn(CONTENT_COPY, state) ? CONTENT_COPY[state] : CONTENT_COPY.lost", "CONTENT_COPY[state] ?? CONTENT_COPY.lost", [C.l05]),
  m("C2-X14", "pending has no sentence", R, '  pending: "This request\'s content is still being processed. Try again shortly.",\n', "", [C.l05]),
];

function copy() {
  const root = mkdtempSync(join(tmpdir(), "c2-mutants-"));
  cpSync(lab, root, { recursive: true, filter: (s) => !/(node_modules|\.next)(\/|$)/.test(s.slice(lab.length)) });
  symlinkSync(join(lab, "node_modules"), join(root, "node_modules"), "dir");
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
  const pristine = readFileSync(join(lab, mutant.file), "utf8");
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

// Every case in the suite is named by at least one mutant, and every named case exists.
const declared = new Set(MUTANTS.flatMap((x) => x.cases));
const baseline = await run(lab);
const cases = [...baseline.out.matchAll(/^ *ok \d+ - (C2-\S+ .*)$/gm)].map((x) => x[1].trim());
const problems = [
  ...(baseline.code === 0 ? [] : ["the unmutated suite does not pass"]),
  ...cases.filter((name) => !declared.has(name)).map((name) => `no mutant names "${name}"`),
  ...[...declared].filter((name) => !cases.includes(name)).map((name) => `a mutant names a missing case "${name}"`),
];
const selected = only === null ? MUTANTS : MUTANTS.filter((x) => only.includes(x.id));
if (selected.length === 0) problems.push("--only matched no mutant");
for (const problem of problems) console.log(`FAIL ${problem}`);
const bad = problems.length;
let survivors = 0;
for (const mutant of selected) {
  const verdict = await judge(mutant);
  if (!verdict.startsWith("killed")) survivors += 1;
  console.log(`${verdict.startsWith("killed") ? "killed " : "NOT KILLED"} ${mutant.id} ${mutant.what} — ${verdict}`);
}
console.log(`\n${cases.length} cases, all named: ${bad === 0}; ${selected.length} mutants, ${selected.length - survivors} killed, ${survivors} not killed`);
process.exit(bad === 0 && survivors === 0 ? 0 : 1);
