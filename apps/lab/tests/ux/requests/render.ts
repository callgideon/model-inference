/**
 * UX-05 fixture harness: render the real request pages to static HTML outside Next. `.tsx` is
 * transpiled with the Lab's TypeScript and `@/` resolves to the Lab root. The page-level seams are
 * stubbed from `globalThis.ux05` (the session workspace, the trace port, feedback and judge reads), so
 * a case can see every read a page made and nothing can reach a network, a database or a session.
 * Pattern: apps/app/tests/ux/first-call/render.ts (UX-04).
 */
import { existsSync, readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const LAB = fileURLToPath(new URL("../../../", import.meta.url));
const require = nodeModule.createRequire(join(LAB, "package.json"));
const ts = require("typescript") as typeof import("typescript");

type Context = { parentURL?: string };
type Resolved = { url: string; shortCircuit?: boolean };
type Loaded = { format: string; source?: string | Uint8Array; shortCircuit?: boolean };
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: {
    resolve(specifier: string, context: Context, next: (specifier: string, context: Context) => Resolved): Resolved;
    load(url: string, context: Context, next: (url: string, context: Context) => Loaded): Loaded;
  }): void;
};

/** What a fixture provides and what the stubs record. */
export type World = {
  workspace: { providerId: string; role: string };
  list?: unknown;
  detail?: unknown;
  feedback?: unknown;
  judge?: unknown;
  calls: unknown[][];
};
const STUBS: Record<string, string> = {
  "next/link": `import { createElement } from "react";
export default function Link({ href, prefetch, ...props }) { return createElement("a", { href: String(href), ...props }); }`,
  "@/lib/auth/guard": `export async function requireProviderWorkspace() { return globalThis.ux05.workspace; }`,
  "@/components/traces/detail/port": `export function tracePorts() {
  const w = globalThis.ux05;
  return { traces: {
    async list(actor, cursor, filter) { w.calls.push(["list", actor, cursor, filter]); return w.list; },
    async detail(actor, id) { w.calls.push(["detail", actor, id]); return w.detail; },
  } };
}`,
  "@/lib/services/review/actions": `export async function reviewRequestFeedback(id) { globalThis.ux05.calls.push(["feedback", id]); return globalThis.ux05.feedback; }`,
  "@/components/traces/judge/port": `export function judgePort() {
  return { async runs(actor, id) { globalThis.ux05.calls.push(["judge", actor, id]); return globalThis.ux05.judge; } };
}`,
};

function probe(base: string): string | null {
  for (const candidate of [base, `${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")]) {
    if (existsSync(candidate) && /\.(tsx?|css)$/.test(candidate)) return candidate;
  }
  return null;
}

let registered = false;
function register(): void {
  if (registered) return;
  registered = true;
  registerHooks({
    resolve(specifier, context, next) {
      if (Object.hasOwn(STUBS, specifier)) return { url: `ux05-stub:${specifier}`, shortCircuit: true };
      if (context.parentURL?.startsWith("ux05-stub:")) return next(specifier, { ...context, parentURL: pathToFileURL(join(LAB, "package.json")).href });
      const parent = context.parentURL?.startsWith("file:") ? fileURLToPath(context.parentURL) : null;
      const local = specifier.startsWith("@/")
        ? probe(join(LAB, specifier.slice(2)))
        : parent && specifier.startsWith(".") && (parent.endsWith(".tsx") || !/\.[a-z]+$/.test(specifier))
          ? probe(join(parent, "..", specifier))
          : null;
      if (local) return { url: pathToFileURL(local).href, shortCircuit: true };
      return next(specifier, context);
    },
    load(url, context, next) {
      if (url.startsWith("ux05-stub:")) return { format: "module", source: STUBS[url.slice("ux05-stub:".length)], shortCircuit: true };
      // A CSS module's class names are their own keys: markup can be read, nothing is styled.
      if (url.endsWith(".module.css")) return { format: "module", source: "export default new Proxy({}, { get: (_, k) => String(k) });", shortCircuit: true };
      if (url.endsWith(".tsx")) {
        const { outputText } = ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
          compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
          fileName: fileURLToPath(url),
        });
        return { format: "module", source: outputText, shortCircuit: true };
      }
      return next(url, context);
    },
  });
}

/** Import a Lab module (path relative to the Lab root) through the hooks. */
export async function lab<T>(path: string): Promise<T> {
  register();
  return (await import(pathToFileURL(join(LAB, path)).href)) as T;
}

/** A fresh world for one case; the stubs read it. */
export function fixture(over: Partial<World>): World {
  const w: World = { workspace: { providerId: "a0000001-0000-4000-8000-000000000001", role: "developer" }, calls: [], ...over };
  (globalThis as unknown as { ux05: World }).ux05 = w;
  return w;
}

/** Render an element (or a server component's awaited element) to static HTML. */
export async function html(element: unknown): Promise<string> {
  register();
  const { renderToStaticMarkup } = (await import(require.resolve("react-dom/server"))) as { renderToStaticMarkup(e: unknown): string };
  return renderToStaticMarkup(element);
}

/** The text a reader sees: tags dropped, entities decoded, whitespace collapsed. */
export function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, " ")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
