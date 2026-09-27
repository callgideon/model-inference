// The Lab mutant harness (R32; LANE-RULES addendum), shared by tests/l/shell and tests/l/ui: every
// decision is one edit that a case it names must fail by assertion. A stale `find`, a load failure or
// a failure in an undeclared case is not a kill. Pattern and judging follow apps/app/tests/u/run-mutants.mjs.
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
export const m = (id, what, file, find, replace, cases) => ({ id, what, file, find, replace, cases });

export async function runMutants({ suite, prefix, mutants }) {
  const args = process.argv.slice(2);
  const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
  const SUITE = suite;
  const MUTANTS = mutants;
  function copy() {
    const root = mkdtempSync(join(tmpdir(), "lab-mutants-"));
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
  const cases = [...baseline.out.matchAll(new RegExp(`^ *ok \\d+ - (${prefix}-\\S+ .*)$`, "gm"))].map((x) => x[1].trim());
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
  return bad === 0 && survivors === 0 ? 0 : 1;
}
