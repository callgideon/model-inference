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
