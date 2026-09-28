// Self-check of the shared mutant harness (WR-P4-5), outside every mutant SUITE: a deep-equal kill,
// whose diff prints its own "..." lines, still reads as an assertion failure, and a replacement is
// written literally.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { failed, mutate } from "./harness.mjs";

test("harness: a deep-equal kill is reported as an assertion failure; replacements are literal", () => {
  const dir = mkdtempSync(join(tmpdir(), "lab-harness-"));
  try {
    const file = join(dir, "deep.test.mjs");
    writeFileSync(
      file,
      `import assert from "node:assert/strict";
import test from "node:test";
test("X-01 deep", () => {
  const a = Array.from({ length: 40 }, (_, i) => ({ i }));
  assert.deepEqual(a.map((x, i) => (i === 20 ? { i: -1 } : x)), a);
});
`,
    );
    // Run it as the harness does, from outside a test runner (NODE_TEST_CONTEXT would redirect its report).
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const out = spawnSync(process.execPath, ["--test", "--test-reporter=tap", file], { encoding: "utf8", env }).stdout;
    assert.match(out, /^ {4}\.\.\.$/m, "the diff prints a deeper '...' line");
    assert.deepEqual(failed(out).find((f: { name: string }) => f.name === "X-01 deep"), { name: "X-01 deep", assertion: true });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  assert.equal(mutate("a X b", "X", "$`$'$&"), "a $`$'$& b");
});
