// UX-00: the synthetic harness project (tests/ux/browser.ts serves it). Its build output goes under the
// Lab's own .next (ignored by git and eslint), never beside these sources.
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import type { NextConfig } from "next";

const lab = resolve(import.meta.dirname, "../../..");
// Turbopack refuses files outside its root; a mutant runner's copy links node_modules to the real
// install, and the Lab's proxy imports the linked generated client (packages/api-client, AP-09), so
// the root is the deepest directory holding all three.
const held = ["node_modules", "node_modules/@infrx/api-client"].map((p) => realpathSync(resolve(lab, p)).split(sep));
const parts = lab.split(sep);
let depth = 0;
while (depth < parts.length && held.every((h) => parts[depth] === h[depth])) depth += 1;
const root = parts.slice(0, depth).join(sep) || sep;

const config: NextConfig = {
  distDir: "../../../.next/ux-harness",
  turbopack: { root },
  outputFileTracingRoot: root,
  agentRules: false,
};
export default config;
