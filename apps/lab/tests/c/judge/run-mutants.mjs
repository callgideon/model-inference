#!/usr/bin/env node
// C3L's mutant runner (R32; LANE-RULES addendum): every C3L decision is one edit that a case it names
// must fail by assertion. Judging follows tests/l/shell/run-mutants.mjs; L1's boundary suite runs
// too, because it is what proves each judge server action calls the provider guard itself.
// Usage: node tests/c/judge/run-mutants.mjs [--only ID,ID]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const SUITE = ["tests/c/judge/judge.test.ts", "tests/l/shell/boundary.test.ts"];

const CORE = "lib/services/judge/core.ts";
const ACTIONS = "lib/services/judge/actions.ts";

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
};
const FORGED = "(input.provider_org_id as string) ?? w.providerId";

const m = (id, what, file, find, replace, cases) => ({ id, what, file, find, replace, cases });
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
];

function copy() {
  const root = mkdtempSync(join(tmpdir(), "c3l-mutants-"));
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
    writeFileSync(join(root, mutant.file), pristine.replace(mutant.find, () => mutant.replace));
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
const cases = [...baseline.out.matchAll(/^ *ok \d+ - ((?:C3L|L1-B01)\S* .*)$/gm)].map((x) => x[1].trim());
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
