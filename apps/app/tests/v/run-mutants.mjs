#!/usr/bin/env node
// R32 for track V: this suite's own test.
//
// Every invariant `tests/v` names must be *killable*. Each mutant in `mutants.json` is one
// single-edit change to a V1 module, applied to a temporary copy of the console, and it must be
// caught by a case the mutant **declares** — failing on an assertion, not by falling over.
//
// What is NOT a kill, and fails the run:
//   - no declared case failed (the mutant survived: the invariant is claimed, not checked);
//   - the `find` text no longer matches, or matches more than once (a stale mutant tests nothing);
//   - the declared case failed by exception rather than assertion (the copy is broken, not caught);
//   - every case failed (the copy is broken).
//
// Usage: node tests/v/run-mutants.mjs [--only ID,ID] [--keep]
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");
const args = process.argv.slice(2);
const only = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const keep = args.includes("--keep");

const SUITE = ["tests/v/trace-query.test.ts", "tests/v/trace-sequence.test.ts", "tests/v/trace-view-model.test.ts"];

const catalogue = JSON.parse(readFileSync(join(here, "mutants.json"), "utf8"));
const mutants = catalogue.mutants.filter((mutant) => only === null || only.includes(mutant.id));

/** One pristine copy; each mutant is written into it and reverted straight after its run. */
const root = mkdtempSync(join(tmpdir(), "v1-mutants-"));
const app = join(root, "app");
cpSync(appRoot, app, {
  recursive: true,
  dereference: false,
  filter: (source) => !/(node_modules|\.next|\.git)(\/|$)/.test(source.slice(appRoot.length)),
});
symlinkSync(join(appRoot, "node_modules"), join(app, "node_modules"), "dir");

/** The failing cases and how each failed: an assertion, or something falling over. */
function failures(out) {
  const lines = out.split("\n");
  const found = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = /^\s*not ok \d+ - (.*)$/.exec(lines[index]);
    if (match === null) continue;
    const diagnostic = lines.slice(index + 1, index + 40).join("\n").split(/^\s*(?:not )?ok \d+ - /m)[0];
    found.push({
      name: match[1].trim(),
      how: /code: 'ERR_ASSERTION'/.test(diagnostic) ? "assertion" : "error",
    });
  }
  return found;
}

function passing(out) {
  return [...out.matchAll(/^\s*ok \d+ - (.*)$/gm)].map((match) => match[1].trim());
}

let failed = 0;
for (const mutant of mutants) {
  const file = join(app, mutant.file);
  const original = readFileSync(file, "utf8");
  const occurrences = original.split(mutant.find).length - 1;
  if (occurrences !== 1) {
    console.log(`RUNNER-ERROR ${mutant.id}: \`find\` matched ${occurrences} times in ${mutant.file}`);
    failed += 1;
    continue;
  }
  writeFileSync(file, original.replace(mutant.find, mutant.replace));
  const run = spawnSync(process.execPath, ["--test", "--test-reporter=tap", ...SUITE], {
    cwd: app,
    encoding: "utf8",
    timeout: 120_000,
  });
  writeFileSync(file, original);

  const out = `${run.stdout ?? ""}${run.stderr ?? ""}`;
  const broke = failures(out);
  const declared = broke.filter((entry) => mutant.cases.includes(entry.name));
  const killers = declared.filter((entry) => entry.how === "assertion");

  // A whole file failing is the copy not loading, parsing or stripping: it tells us nothing about
  // the invariant, whatever else passed.
  const brokenFiles = broke.filter((entry) => entry.name.endsWith(".test.ts")).map((entry) => entry.name);

  if (brokenFiles.length > 0) {
    console.log(`RUNNER-ERROR ${mutant.id}: ${brokenFiles.join(", ")} did not run — the copy is broken, not caught`);
    failed += 1;
  } else if (broke.length > 0 && passing(out).length === 0) {
    console.log(`RUNNER-ERROR ${mutant.id}: every case failed — the copy is broken, not caught`);
    failed += 1;
  } else if (killers.length > 0) {
    console.log(`killed       ${mutant.id}  by "${killers[0].name}"`);
  } else if (declared.length > 0) {
    console.log(
      `RUNNER-ERROR ${mutant.id}: declared case "${declared[0].name}" failed by ${declared[0].how}, not an assertion`,
    );
    failed += 1;
  } else {
    console.log(
      `SURVIVED     ${mutant.id}: ${mutant.note}\n             declared ${JSON.stringify(mutant.cases)}, failing ${JSON.stringify(broke.map((entry) => entry.name))}`,
    );
    failed += 1;
  }
}

if (!keep) rmSync(root, { recursive: true, force: true });
console.log(`\n${mutants.length - failed}/${mutants.length} mutants killed by a declared case.`);
process.exit(failed === 0 ? 0 : 1);
