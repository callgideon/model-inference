// L1 LAB-ACCESS + SPLIT-CONTRACT, read as source: a layout does not stop its pages or actions from
// running (Next's Partial Rendering), so every entry point calls the guard itself.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const GUARD = /\b(requireProviderWorkspace|requireProviderSession|providerAccessForRequest)\(/;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (/^(node_modules|\.next)$/.test(name)) return [];
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}
const sources = () =>
  ["app", "lib"].flatMap((d) => walk(join(lab, d))).filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.ts$/.test(p))
    .map((path) => ({ path: relative(lab, path), source: readFileSync(path, "utf8") }));

/** Entry points with no guard: pages, route handlers, provider layouts, and each exported server action. */
export function unguarded(files: { path: string; source: string }[]): string[] {
  const out: string[] = [];
  for (const { path, source } of files) {
    if (/^\s*["']use server["']/.test(source)) {
      for (const chunk of source.split(/^export /m).slice(1)) {
        if (/^async function/.test(chunk) && !GUARD.test(chunk)) out.push(`${path}: ${chunk.split("(")[0]}`);
      }
    } else if (/(^|\/)(page\.tsx|route\.ts)$/.test(path) || /^app\/\(provider\)\/.*layout\.tsx$/.test(path)) {
      if (!GUARD.test(source)) out.push(path);
    }
  }
  return out;
}

test("L1-B01 every page, route, provider layout and server action calls the provider guard", () => {
  const files = sources();
  assert.ok(files.some((f) => f.path === "lib/auth/actions.ts"), "the selection action exists");
  assert.deepEqual(unguarded(files), []);
  // The check itself: an unguarded page and an unguarded action are both caught.
  assert.deepEqual(
    unguarded([
      { path: "app/(provider)/x/page.tsx", source: "export default function P() {}" },
      { path: "lib/a.ts", source: '"use server";\nexport async function ok() { await requireProviderSession(); }\nexport async function bad() {}' },
    ]),
    ["app/(provider)/x/page.tsx", "lib/a.ts: async function bad"],
  );
});

test("L1-B02 the provider layout renders its children only for a ready workspace", () => {
  const layout = readFileSync(join(lab, "app/(provider)/layout.tsx"), "utf8");
  assert.equal(layout.split("{children}").length - 1, 1);
  const ready = layout.indexOf('access.kind === "ready"');
  assert.ok(ready !== -1 && ready < layout.indexOf("{children}"));
  assert.match(layout, /export const dynamic = "force-dynamic";/);
});

test("L1-B03 the selection action stores the membership it validated, never the submitted value", () => {
  const action = readFileSync(join(lab, "lib/auth/actions.ts"), "utf8");
  assert.match(action, /chooseWorkspace\(access, formData\.get\("providerId"\)\)/);
  assert.match(action, /\.set\(WORKSPACE_COOKIE, chosen\.providerId, workspaceCookieOptions\(config\)\)/);
  assert.equal(action.split(".set(").length - 1, 1);
});

test("L1-B04 the Lab never imports the App: shared code comes only from packages/shared", () => {
  const offenders = sources().filter(({ source }) => /from\s+["'][^"']*apps\/app|from\s+["'](\.\.\/)+app\//.test(source));
  assert.deepEqual(offenders.map((f) => f.path), []);
});
