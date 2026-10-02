// UX-00 (App half): the additive semantic tokens and the synthetic harness's isolation. Each case names
// what it catches.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import test from "node:test";

const app = resolve(import.meta.dirname, "../..");
const css = readFileSync(join(app, "app/globals.css"), "utf8");

function block(selector: string): string {
  const hit = new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`).exec(css);
  assert.ok(hit, `no ${selector} block`);
  return hit[1];
}

test("UXA-S01 success and warning are semantic tokens in both themes and Tailwind colours, beside the existing ones", () => {
  for (const name of ["success", "warning"]) {
    assert.match(block(":root"), new RegExp(`--${name}: oklch\\(`), `:root --${name}`);
    assert.match(block(".dark"), new RegExp(`--${name}: oklch\\(`), `.dark --${name}`);
    assert.match(css, new RegExp(`--color-${name}: var\\(--${name}\\);`), `@theme --color-${name}`);
  }
  assert.notEqual(/--success: ([^;]+);/.exec(block(".dark"))?.[1], /--destructive: ([^;]+);/.exec(block(".dark"))?.[1], "success is not the error colour");
});

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

test("UXA-S02 no production module reaches the synthetic harness or its fixtures", () => {
  const offenders = ["app", "lib", "components"]
    .flatMap((d) => walk(join(app, d)))
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.ts$/.test(p))
    // tests/ux only: lib/contracts/conformance.ts reads tests/contracts' shared cases on purpose.
    .filter((p) => /from\s+["'][^"']*tests\/ux\//.test(readFileSync(p, "utf8")))
    .map((p) => relative(app, p));
  assert.deepEqual(offenders, []);
});
