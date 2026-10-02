// UX-06/UX-09: the improve lane's synthetic harness project (tests/ux/browser.ts serves it). Its build output goes under the
// Lab's own .next (ignored by git and eslint), never beside these sources.
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import type { NextConfig } from "next";

const lab = resolve(import.meta.dirname, "../../../..");
// Turbopack refuses files outside its root; a mutant runner's copy links node_modules to the real
// install, so the root is the deepest directory holding both.
const modules = realpathSync(resolve(lab, "node_modules")).split(sep);
const parts = lab.split(sep);
let depth = 0;
while (depth < parts.length && parts[depth] === modules[depth]) depth += 1;
const root = parts.slice(0, depth).join(sep) || sep;

const config: NextConfig = {
  distDir: "../../../../.next/ux-improve",
  turbopack: { root },
  outputFileTracingRoot: root,
  agentRules: false,
};
export default config;
