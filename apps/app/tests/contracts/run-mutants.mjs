#!/usr/bin/env node
// R32: the conformance suite's own test.
//
// Every invariant a conformance case names must be *killable*: if a single-edit change to the
// implementation can break the invariant and the suite still passes, the suite is claiming something
// it does not check. This runner applies each declared mutant to a temporary copy of the console and
// requires it to fail at least one case of the EXPORTED conformance functions — not the fake-only
// tests, because those are not what track C runs.
//
// Run with `pnpm test:mutants`. It is deliberately not part of `pnpm test`: it costs one Node
// process per mutant.
//
// Usage: node tests/contracts/run-mutants.mjs [--only ID,ID] [--jobs N] [--keep]
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");
const catalogue = JSON.parse(readFileSync(join(here, "mutants.json"), "utf8"));

const args = process.argv.slice(2);
const only = new Set(
  (args.includes("--only") ? args[args.indexOf("--only") + 1] : "").split(",").filter((id) => id !== ""),
);
const jobs = Math.max(1, Number(args.includes("--jobs") ? args[args.indexOf("--jobs") + 1] : 4) || 4);
const keep = args.includes("--keep");

const mutants = catalogue.mutants.filter((mutant) => only.size === 0 || only.has(mutant.id));
if (mutants.length === 0) {
  console.error("no mutants selected");
  process.exit(2);
}

/**
 * The entry point each run executes: only the exported conformance functions, against the fake.
 * Written into the copy so the mutant is judged by what C would run and nothing else.
 */
const ENTRY = `import { runConsoleServicesConformance } from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";

runConsoleServicesConformance(() => {
  const services = createFakeConsoleServices();
  return { services, sessions: services.sessions, ids: services.ids };
}, "mutation target");
`;

function prepareCopy() {
  const root = mkdtempSync(join(tmpdir(), "f2ts-mutants-"));
  const app = join(root, "app");
  cpSync(appRoot, app, {
    recursive: true,
    dereference: false,
    filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
  });
  symlinkSync(join(appRoot, "node_modules"), join(app, "node_modules"), "dir");
  writeFileSync(join(app, "tests/contracts/__conformance-only.test.ts"), ENTRY);
  return { root, app };
}

function runSuite(app) {
  return new Promise((done) => {
    const child = spawn(
      process.execPath,
      ["--test", "--test-reporter=tap", "tests/contracts/__conformance-only.test.ts"],
      { cwd: app, stdio: ["ignore", "pipe", "pipe"] },
    );
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
    });
    child.stderr.on("data", (chunk) => {
      out += chunk;
    });
    child.on("close", (code) => {
      const failed = [...out.matchAll(/^ {4}not ok \d+ - (.*)$/gm)].map((match) => match[1]);
      done({ code, failed, out });
    });
  });
}

function applyMutant(app, mutant) {
  const target = join(app, mutant.file);
  const pristine = readFileSync(join(appRoot, mutant.file), "utf8");
  const occurrences = pristine.split(mutant.find).length - 1;
  if (occurrences === 0) return { ok: false, why: "its `find` text is not in the source any more" };
  if (occurrences > 1) return { ok: false, why: `its \`find\` text appears ${occurrences} times, so the edit is ambiguous` };
  writeFileSync(target, pristine.replace(mutant.find, mutant.replace));
  return { ok: true, restore: () => writeFileSync(target, pristine) };
}

const started = Date.now();
const { root, app } = prepareCopy();
let exitCode = 0;
try {
  // The unmutated suite must pass in the copy, or every result below is meaningless.
  const baseline = await runSuite(app);
  if (baseline.code !== 0) {
    console.error("the conformance suite does not pass unmutated — fix that first:\n");
    console.error(baseline.out.split("\n").filter((line) => line.startsWith("not ok") || line.includes("Error")).join("\n"));
    process.exit(2);
  }
  console.log(`baseline: conformance suite passes (${mutants.length} mutants to apply, ${jobs} at a time)\n`);

  // One worktree copy per job, so mutants can run in parallel without racing on the same file.
  const workers = [{ root, app }];
  for (let i = 1; i < jobs; i += 1) workers.push(prepareCopy());

  const results = [];
  let next = 0;
  await Promise.all(
    workers.map(async (worker) => {
      for (;;) {
        const index = next;
        next += 1;
        if (index >= mutants.length) return;
        const mutant = mutants[index];
        const applied = applyMutant(worker.app, mutant);
        if (!applied.ok) {
          results.push({ mutant, stale: applied.why });
          continue;
        }
        const run = await runSuite(worker.app);
        applied.restore();
        results.push({ mutant, killed: run.code !== 0, failed: run.failed });
      }
    }),
  );

  results.sort((a, b) => a.mutant.id.localeCompare(b.mutant.id));
  const survivors = [];
  const stale = [];
  for (const result of results) {
    if (result.stale !== undefined) {
      stale.push(result);
      console.log(`STALE    ${result.mutant.id.padEnd(12)} ${result.stale}`);
      continue;
    }
    if (result.killed) {
      const by = result.failed[0] ?? "(a case that reported no name)";
      console.log(`killed   ${result.mutant.id.padEnd(12)} ${by}`);
    } else {
      survivors.push(result);
      console.log(`SURVIVED ${result.mutant.id.padEnd(12)} ${result.mutant.note}`);
    }
  }

  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  console.log(
    `\n${results.length} mutants, ${results.length - survivors.length - stale.length} killed by the exported conformance suite, ` +
      `${survivors.length} survived, ${stale.length} stale, ${seconds}s`,
  );
  if (survivors.length > 0) {
    console.error(
      "\nA surviving mutant means the exported suite names an invariant it cannot enforce, so track C\n" +
        "could pass the suite with that defect. Add or strengthen a case, or delete the claim:",
    );
    for (const result of survivors) console.error(`  ${result.mutant.id}: ${result.mutant.note}`);
    exitCode = 1;
  }
  if (stale.length > 0) {
    console.error("\nA stale mutant no longer matches the source, so it tests nothing. Update mutants.json:");
    for (const result of stale) console.error(`  ${result.mutant.id}: ${result.stale}`);
    exitCode = 1;
  }
  if (!keep) for (const worker of workers) rmSync(worker.root, { recursive: true, force: true });
  else console.log(`\ncopies kept at ${workers.map((worker) => worker.root).join(", ")}`);
} finally {
  if (!keep) rmSync(root, { recursive: true, force: true });
}
process.exit(exitCode);
