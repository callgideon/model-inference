// UX-00: the synthetic harness project (tests/ux/browser.ts serves it). Its build output goes under the
// Lab's own .next (ignored by git and eslint), never beside these sources.
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import type { NextConfig } from "next";

const lab = resolve(import.meta.dirname, "../../..");
// Turbopack refuses files outside its root; a mutant runner's copy links node_modules to the real
// install, so the root is the deepest directory holding both.
const modules = realpathSync(resolve(lab, "node_modules")).split(sep);
const shared = lab.split(sep).filter((part, i) => modules[i] === part && lab.split(sep).slice(0, i).every((p, j) => modules[j] === p));
const root = shared.join(sep) || sep;

const config: NextConfig = {
  distDir: "../../../.next/ux-harness",
  turbopack: { root },
  outputFileTracingRoot: root,
  agentRules: false,
};
export default config;
