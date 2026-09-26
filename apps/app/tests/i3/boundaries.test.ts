// node --test "tests/**/*.test.ts"
//
// I3 (review I3R-6): every error boundary under app/ reports the error it catches through I3's one
// report client (lib/deploy/error-view.ts → POST /api/client-errors), with only the sanitised
// digest, route and class name — never the message. The nearer segment boundaries (usage, billing,
// traces) catch first, so one that does not report hides every browser error of its route.
//
// Each boundary is rendered for real (TypeScript's transpiler, React's server renderer). The server
// renderer never runs effects, so for lib/deploy/ only, `react`'s useEffect runs its callback at
// once: the report the browser would send after mounting is sent during the render, to a stub fetch.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import * as nodeModule from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { ErrorProps } from "../../lib/deploy/error-view.ts";
import { REPORT_PATH } from "../../lib/deploy/report.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Loaded = { format: string; source: string | Uint8Array; shortCircuit?: boolean };
type Context = { parentURL?: string };
// This @types/node predates `module.registerHooks` (Node 22.15; engines >= 22.18).
const { createRequire, registerHooks } = nodeModule as typeof nodeModule & {
  registerHooks(hooks: {
    resolve(specifier: string, context: Context, next: (specifier: string, context: Context) => Resolved): Resolved;
    load(url: string, context: object, next: (url: string, context: object) => Loaded): Loaded;
  }): void;
};
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const requireApp = createRequire(`${appRoot}/package.json`);
const isFile = (path: string) => statSync(path, { throwIfNoEntry: false })?.isFile() === true;
const deploy = join(appRoot, "lib", "deploy") + "/";
const react = pathToFileURL(requireApp.resolve("react")).href;
const EFFECTS_NOW = `data:text/javascript,${encodeURIComponent(
  `import React from ${JSON.stringify(react)}; export * from ${JSON.stringify(react)}; export default React;` +
    "export const useEffect = (effect) => { effect(); };",
)}`;
registerHooks({
  resolve(specifier, context, next) {
    const parent = context.parentURL?.startsWith("file:") ? fileURLToPath(context.parentURL) : null;
    if (specifier === "react" && parent?.startsWith(deploy)) return { url: EFFECTS_NOW, shortCircuit: true };
    // `next/link` has no ESM exports map; resolve it as Next's bundler does.
    if (specifier.startsWith("next/")) return { url: pathToFileURL(requireApp.resolve(specifier)).href, shortCircuit: true };
    const base = specifier.startsWith("@/")
      ? resolve(appRoot, specifier.slice(2))
      : parent !== null && !parent.includes("node_modules") && /^\.\.?\//.test(specifier)
        ? resolve(dirname(parent), specifier)
        : null;
    const file = base === null ? undefined : [base, `${base}.tsx`, `${base}.ts`].find(isFile);
    return file === undefined ? next(specifier, context) : { url: pathToFileURL(file).href, shortCircuit: true };
  },
  load(url, context, next) {
    if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
    if (!url.endsWith(".tsx")) return next(url, context);
    const ts = requireApp("typescript");
    const source = ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { format: "module", source, shortCircuit: true };
  },
});

/** Every error.tsx / global-error.tsx under app/, so a boundary added later is held to the same rule. */
function boundaries(dir = join(appRoot, "app")): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? boundaries(join(dir, entry.name))
      : /^(?:global-)?error\.tsx$/.test(entry.name) ? [join(dir, entry.name)] : [],
  );
}

const KEY = "sk-infrx-AbCdEfGh0123456789AbCdEfGh0123456789AbCd";
const EMAIL = "someone.person@example.com";

test("I3-BOUND-01 every error boundary under app/ reports once through the report client, with only digest, route and name", async () => {
  // Catches (review I3R-6): a segment boundary (usage, billing, traces) that renders its copy and
  // never reports, so browser throws on its route produce no app_error line; and a boundary that
  // reports anything more than the sanitised fields (the message, the raw path).
  const files = boundaries();
  assert.ok(files.length >= 5, `found only ${files.map((f) => relative(appRoot, f)).join(", ")}`);
  const sent: { url: unknown; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  const hadWindow = "window" in globalThis;
  globalThis.fetch = (async (url: unknown, init: RequestInit) => {
    sent.push({ url, init });
    return new Response(null, { status: 204 });
  }) as typeof fetch;
  Object.assign(globalThis, { window: { location: { pathname: `/usage/${encodeURIComponent(EMAIL)}` } } });
  try {
    for (const file of files) {
      const name = relative(appRoot, file);
      const { default: Boundary } = (await import(pathToFileURL(file).href)) as { default: ComponentType<ErrorProps> };
      const error = Object.assign(new TypeError(`database said ${EMAIL} ${KEY}`), { digest: "4242@E1" });
      sent.length = 0;
      const html = renderToStaticMarkup(createElement(Boundary, { error, retry: () => undefined }));
      assert.ok(!html.includes(EMAIL) && !html.includes(KEY), `${name} renders the message`);
      assert.equal(sent.length, 1, `${name} sent ${sent.length} reports`);
      const [{ url, init }] = sent;
      assert.equal(url, REPORT_PATH, name);
      assert.equal(init.method, "POST", name);
      assert.equal(init.keepalive, true, `${name}: a report must survive the retry's navigation`);
      assert.deepEqual(init.headers, { "content-type": "application/json" }, name);
      assert.deepEqual(JSON.parse(String(init.body)), { digest: "4242@E1", route: "/usage/[id]", name: "TypeError" }, name);
    }
  } finally {
    globalThis.fetch = realFetch;
    if (!hadWindow) delete (globalThis as { window?: unknown }).window;
  }
});
