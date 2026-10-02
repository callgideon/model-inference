// UX-00: the App's synthetic harness project (tests/ux/browser.ts serves it). Its build output goes
// under the App's own .next (ignored by git and eslint), never beside these sources.
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import type { NextConfig } from "next";

const app = resolve(import.meta.dirname, "../../..");
// Turbopack refuses files outside its root; a mutant runner's copy links node_modules to the real
// install, and the generated API client is a linked package (packages/api-client), so the root is
// the deepest directory holding all three.
const held = [realpathSync(resolve(app, "node_modules")), realpathSync(resolve(app, "node_modules/@infrx/api-client"))].map((p) => p.split(sep));
const parts = app.split(sep);
let depth = 0;
while (depth < parts.length && held.every((other) => parts[depth] === other[depth])) depth += 1;
const root = parts.slice(0, depth).join(sep) || sep;

const config: NextConfig = {
  distDir: "../../../.next/ux-harness",
  turbopack: { root },
  outputFileTracingRoot: root,
  agentRules: false,
};
export default config;
