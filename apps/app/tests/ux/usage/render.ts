/**
 * UX-07 fixture renderer: a real App component to static HTML outside Next, with fixture props. `.tsx`
 * is transpiled with the App's TypeScript, `@/` resolves to the App root, and the two Next seams these
 * components reach are stubbed (`next/link` -> <a>, `next/navigation` -> a no-op router), so nothing
 * here can reach a network, a database or a server action. Client components render their first state
 * (no effects run).
 *
 * ponytail: the module hooks are UX-04's (tests/ux/first-call/render.ts, not exported there); fold the
 * two into one shared helper when the UX lanes merge.
 */
import { existsSync, readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const APP = fileURLToPath(new URL("../../../", import.meta.url));
const require = nodeModule.createRequire(join(APP, "package.json"));

type Context = { parentURL?: string };
type Resolved = { url: string; shortCircuit?: boolean };
type Loaded = { format: string; source?: string | Uint8Array; shortCircuit?: boolean };
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: {
    resolve(specifier: string, context: Context, next: (specifier: string, context: Context) => Resolved): Resolved;
    load(url: string, context: Context, next: (url: string, context: Context) => Loaded): Loaded;
  }): void;
};
const ts = require("typescript") as typeof import("typescript");

const STUBS: Record<string, string> = {
  "next/link": `import { createElement } from "react";
export default function Link({ href, prefetch, ...props }) { return createElement("a", { href: String(href), ...props }); }`,
  "next/navigation": `export const useRouter = () => ({ refresh() {}, push() {}, replace() {} });`,
};

function probe(base: string): string | null {
  for (const candidate of [base, `${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")]) {
    if (existsSync(candidate) && /\.(tsx?|mjs|js)$/.test(candidate)) return candidate;
  }
  return null;
}

let registered = false;
function register(): void {
  if (registered) return;
  registered = true;
  registerHooks({
    resolve(specifier, context, next) {
      if (Object.hasOwn(STUBS, specifier)) return { url: `fixture-stub:${specifier}`, shortCircuit: true };
      if (context.parentURL?.startsWith("fixture-stub:")) return next(specifier, { ...context, parentURL: pathToFileURL(join(APP, "package.json")).href });
      const parent = context.parentURL?.startsWith("file:") ? fileURLToPath(context.parentURL) : null;
      const local = specifier.startsWith("@/")
        ? probe(join(APP, specifier.slice(2)))
        : parent && specifier.startsWith(".") && (parent.endsWith(".tsx") || !/\.[a-z]+$/.test(specifier))
          ? probe(join(parent, "..", specifier))
          : null;
      if (local) return { url: pathToFileURL(local).href, shortCircuit: true };
      return next(specifier, context);
    },
    load(url, context, next) {
      if (url.startsWith("fixture-stub:")) return { format: "module", source: STUBS[url.slice("fixture-stub:".length)], shortCircuit: true };
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

/** `exported` of the App module at `file` (App-relative), rendered with `props`, as static HTML. */
export async function render(file: string, exported: string, props: Record<string, unknown>): Promise<string> {
  register();
  const { createElement } = await import(require.resolve("react"));
  const { renderToStaticMarkup } = await import(require.resolve("react-dom/server"));
  const component = (await import(join(APP, file)))[exported];
  if (typeof component !== "function") throw new Error(`${file} exports no component ${exported}`);
  return renderToStaticMarkup(createElement(component, props)) as string;
}

/** The visible text of rendered HTML, whitespace collapsed (entities decoded for the few React emits). */
export function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}
