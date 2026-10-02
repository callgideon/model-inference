/**
 * UX-04 fixture harness: render the real first-call panel to static HTML outside Next, with the App's
 * own Tailwind CSS, so a browser can measure it (fold.test.ts). `.tsx` is transpiled with the App's
 * TypeScript; `@/` resolves to the App root; the three Next/server seams the panel reaches are stubbed
 * (`next/link` -> <a>, `next/navigation` -> a no-op router, `@/app/actions` -> a refusing action), so
 * nothing here can reach a network, a database or a server action.
 *
 * ponytail: one panel, the App shell approximated by its layout classes and a w-60 sidebar column with
 * system fonts; the batch-3 visual suite (UX-11) owns full-page rendering.
 */
import { existsSync, readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const APP = fileURLToPath(new URL("../../../", import.meta.url));
const { createRequire } = nodeModule;
const require = createRequire(join(APP, "package.json"));

// Node >= 22.15's synchronous module hooks; the pinned @types/node (20) predates them.
type Context = { parentURL?: string; conditions?: string[] };
type Resolved = { url: string; format?: string; shortCircuit?: boolean };
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
  "@/app/actions": `export async function createConsumerKey() { return { ok: false, error: { code: "dependency_unavailable", message: "fixture" } }; }`,
};

function probe(base: string): string | null {
  for (const candidate of [base, `${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")]) {
    if (existsSync(candidate) && !candidate.endsWith("/") && /\.(tsx?|mjs|js)$/.test(candidate)) return candidate;
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
      // A stub's own imports (react) resolve from the App root.
      if (context.parentURL?.startsWith("fixture-stub:")) return next(specifier, { ...context, parentURL: pathToFileURL(join(APP, "package.json")).href });
      const parent = context.parentURL?.startsWith("file:") ? fileURLToPath(context.parentURL) : null;
      const local = specifier.startsWith("@/")
        ? probe(join(APP, specifier.slice(2)))
        : parent && specifier.startsWith(".") && parent.endsWith(".tsx")
          ? probe(join(parent, "..", specifier))
          : parent && specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)
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

/** The App's Tailwind CSS, compiled from app/globals.css the way `next build` does. */
export async function appCss(): Promise<string> {
  const tailwind = require("@tailwindcss/postcss");
  const postcss = createRequire(require.resolve("@tailwindcss/postcss"))("postcss");
  const from = join(APP, "app", "globals.css");
  const result = await postcss([tailwind({ base: APP })]).process(readFileSync(from, "utf8"), { from });
  return result.css as string;
}

/** The Models page's header and first-call panel for `guide`, inside the console shell's layout. */
export async function renderFirstCall(props: Record<string, unknown>): Promise<string> {
  register();
  const { createElement } = await import(require.resolve("react"));
  const { renderToStaticMarkup } = await import(require.resolve("react-dom/server"));
  const { FirstCallPanel } = await import(join(APP, "app/(console)/models/first-call-panel.tsx"));
  const { CreateKeyDialog } = await import(join(APP, "app/(console)/api-keys/create-key-dialog.tsx"));
  const { PageHeader } = await import(join(APP, "components/page-header.tsx"));
  const page = readFileSync(join(APP, "app/(console)/models/page.tsx"), "utf8");
  // The header exactly as the page passes it, so a longer header moves the panel down here too.
  const subtitle = /<PageHeader title="Models" subtitle="([^"]*)" \/>/.exec(page)?.[1];
  if (subtitle === undefined) throw new Error("the Models page header is not where the fixture reads it");
  const guide = props.guide as { canCreate: boolean };
  const main = renderToStaticMarkup(
    createElement("div", null,
      createElement(PageHeader, { title: "Models", subtitle }),
      createElement(FirstCallPanel, { ...props, createKey: guide.canCreate ? createElement(CreateKeyDialog) : null }),
    ),
  );
  return `<div class="flex min-h-svh flex-col md:flex-row"><aside class="w-60 shrink-0 border-r" aria-label="sidebar placeholder"></aside><main class="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8"><div class="mx-auto max-w-6xl">${main}</div></main></div>`;
}
