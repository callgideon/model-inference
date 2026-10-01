// W6 lab-A (LAB-01, LAB-02): the Lab's one shared package. `@infrx/shared` is a pnpm `link:` to
// packages/shared, so the installed package is the live directory and an edit there can never be
// hidden behind a stale `file:` copy (the TS2307 at the 2026-10-01 tip). The package carries only the
// Lab contracts; the App's contracts are not copied into it, and the Lab defines the two types it used.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const LINK = "link:../../packages/shared";
// The installed package: in a mutant copy node_modules is a symlink to the real install.
const installed = realpathSync(join(lab, "node_modules/@infrx/shared"));

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (/^(node_modules|\.next)$/.test(name)) return [];
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

test("W6A-01 @infrx/shared is a link: to packages/shared in package.json and the lockfile, never a file: copy", () => {
  const pkg = JSON.parse(readFileSync(join(lab, "package.json"), "utf8"));
  assert.equal(pkg.dependencies["@infrx/shared"], LINK);
  const lock = readFileSync(join(lab, "pnpm-lock.yaml"), "utf8");
  assert.match(lock, /\n {6}'@infrx\/shared':\n {8}specifier: link:\.\.\/\.\.\/packages\/shared\n {8}version: link:\.\.\/\.\.\/packages\/shared\n/);
  assert.doesNotMatch(lock, /@infrx\/shared@file:/);
  assert.match(installed, /\/packages\/shared$/, "the install is the directory itself, not a .pnpm copy");
});

test("W6A-02 the shared package exports only the Lab contracts, and every Lab import of it resolves", () => {
  const exports: Record<string, string> = JSON.parse(readFileSync(join(installed, "package.json"), "utf8")).exports;
  assert.deepEqual(Object.keys(exports), ["./contracts/lab", "./contracts/lab/fakes", "./contracts/lab/fixtures.json"]);
  assert.equal(existsSync(join(installed, "console")), false, "the App's contracts are not copied into the package");
  // Static, side-effect and dynamic imports, in the source trees and the root files (proxy.ts, next.config.ts).
  const files = [...["app", "lib", "components", "tests"].flatMap((d) => walk(join(lab, d))),
    ...readdirSync(lab).filter((name) => name.endsWith(".ts")).map((name) => join(lab, name))];
  const specifiers = files.filter((p) => /\.(ts|tsx)$/.test(p))
    .flatMap((p) => [...readFileSync(p, "utf8").matchAll(/(?:from\s+|import\s*\(?\s*)["']@infrx\/shared(\/[^"']*)?["']/g)].map((x) => `.${x[1] ?? ""}`));
  for (const s of specifiers) {
    assert.ok(s in exports, `${s} is not an export of @infrx/shared`);
    assert.ok(existsSync(join(installed, exports[s])), `${s} -> ${exports[s]} is missing`);
  }
});

// W6A-R1: the Lab's local loss vocabulary (LAB-02) is the frozen trace contract's, read the way
// apps/infrx-api/tests/contracts/test_parity_console.py reads the App's types.ts: by parsing both sources.
// The repo root is found through the linked package, so a mutant copy of apps/lab still reaches records.py.
test("W6A-03 the Lab's TraceLossReason union is exactly the contract's TraceLossReason values", () => {
  const view = readFileSync(join(lab, "components/traces/list/view-model.ts"), "utf8");
  const union = view.match(/\ntype TraceLossReason =([^;]*);/);
  assert.ok(union, "the TraceLossReason union is not found in view-model.ts");
  const ts = [...union[1].matchAll(/\|\s*"([^"]+)"/g)].map((x) => x[1]);
  const records = readFileSync(join(installed, "../../apps/infrx-api/infrx/contracts/records.py"), "utf8");
  const body = records.match(/\nclass TraceLossReason\(enum\.StrEnum\):\n((?: {4}\w+ = "[^"]*".*\n)+)/);
  assert.ok(body, "class TraceLossReason is not found in records.py");
  const py = [...body[1].matchAll(/= "([^"]*)"/g)].map((x) => x[1]);
  assert.ok(py.length > 0);
  assert.deepEqual([...ts].sort(), [...py].sort());
  assert.equal(new Set(ts).size, ts.length, "a reason is listed twice");
});
