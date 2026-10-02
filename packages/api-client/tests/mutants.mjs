#!/usr/bin/env node
// AP-00 00c (R32 for the transport): one single-edit defect per decision `src/transport.ts` makes,
// each of which a NAMED case of tests/transport.test.ts must fail. A mutant whose anchor is not
// found exactly once, or that no named case notices, fails the run.
//
//     pnpm test:mutants            (one node process per mutant, on a temporary copy)
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const FILE = "src/transport.ts";
const SEND = "a call forwards the session, a request id and no-store, and types the answer";
const MUTATE = "a mutation sends its JSON body, idempotency key and encoded path parameters";
const FAIL = "each failure format maps to its own kind; an unreadable answer is unavailable";
const MUTANTS = [
  ["cacheable", 'cache: "no-store", ', "", SEND],
  ["request_id_dropped", '"x-request-id": requestId', '"x-request-id": "fixed"', SEND],
  ["session_not_forwarded", "if (session.token) headers.authorization", "if (false) headers.authorization", SEND],
  ["path_parameter_unencoded", "return encodeURIComponent(value);", "return value;", MUTATE],
  ["idempotency_key_dropped", 'if (init.idempotencyKey) headers["idempotency-key"]', 'if (false) headers["idempotency-key"]', MUTATE],
  ["r270_read_as_openai", '&& typeof e.retryable === "boolean") {', "&& false) {", FAIL],
  ["refusal_unrecognised", 'if (isObject(body) && typeof body.refusal === "string")', "if (false)", FAIL],
  ["malformed_answer_succeeds", "          return { ok: false, requestId, error: { kind: \"unavailable\", status: answer.status, reason: \"malformed\" } };\n",
    "          body = text;\n", FAIL],
  ["failure_status_succeeds", "answer.status >= 200 && answer.status < 300", "answer.status < 500", FAIL],
  ["network_failure_throws", '        return { ok: false, requestId, error: { kind: "unavailable", status: null, reason: "network" } };\n',
    "        throw new Error(\"down\");\n", FAIL],
];

let failed = 0;
for (const [name, old, replacement, testCase] of MUTANTS) {
  const tmp = mkdtempSync(join(tmpdir(), `api-client-mutant-${name}-`));
  try {
    for (const dir of ["src", "tests"]) cpSync(join(ROOT, dir), join(tmp, dir), { recursive: true });
    symlinkSync(join(ROOT, "node_modules"), join(tmp, "node_modules"));
    writeFileSync(join(tmp, "package.json"), readFileSync(join(ROOT, "package.json")));
    const source = readFileSync(join(tmp, FILE), "utf8");
    const found = source.split(old).length - 1;
    let verdict;
    if (found !== 1) verdict = `MISDECLARED (anchor found ${found} times)`;
    else {
      writeFileSync(join(tmp, FILE), source.replace(old, replacement));
      const run = spawnSync(process.execPath, ["--test", "--test-name-pattern", testCase, "tests/transport.test.ts"],
        { cwd: tmp, encoding: "utf8", timeout: 60_000 });
      const noticed = run.stdout.split("\n").some((line) => line.startsWith("not ok") && line.includes(testCase));
      verdict = run.status !== 0 && noticed ? "killed" : `SURVIVED (exit ${run.status})`;
    }
    if (verdict !== "killed") failed += 1;
    console.log(`[${verdict}] ${name}`);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
console.log(`${MUTANTS.length - failed}/${MUTANTS.length} killed`);
process.exit(failed ? 1 : 0);
