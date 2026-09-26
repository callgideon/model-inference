import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const src = (p: string) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
test("U2-W01 the consumer navigation links Settings", () => {
  assert.match(src("components/sidebar.tsx"), /\{ href: "\/settings", label: "Settings", icon: Settings \}/);
});
