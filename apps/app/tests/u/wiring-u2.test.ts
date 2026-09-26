import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const src = (p: string) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
test("U2-W01 the consumer navigation links Settings", () => {
  assert.match(src("components/sidebar.tsx"), /\{ href: "\/settings", label: "Settings", icon: Settings \}/);
});
test("U2-W02 no module remembers or recalls a key's plaintext", () => {
  for (const p of ["lib/keys.ts", "components/snippet.tsx"]) assert.doesNotMatch(src(p), /sessionStorage|rememberKey|recallKey/, p);
});
