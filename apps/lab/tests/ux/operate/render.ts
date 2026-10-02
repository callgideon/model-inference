// UX-03 fixture renderer: the Lab's real provider layout and operate pages rendered to static HTML
// outside Next, with lab.css and the lane's CSS module, so a browser can lay them out at 390/768/1440
// and walk them by keyboard. `.tsx` is transpiled with the Lab's TypeScript; `@/` resolves to the Lab
// root; the Next and server seams are stubbed: next/link -> <a>, next/navigation -> the fixture path,
// the guard -> the fixture access (globalThis.__operateAccess), every server action -> a function
// that does nothing, the wizard's API port -> globalThis.__operateArtifacts. The control records come
// from the real preview fake (LAB_CONTROL_PREVIEW=1), so nothing here reaches a session, a network or
// a service.
//
// ponytail: static markup, no hydration - client islands render their server HTML only (a dialog shows
// its trigger); UX-00's harness (tests/ux/foundations) owns the interactive primitives' behaviour.
import { existsSync, readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { ReactElement } from "react";

export const LAB = fileURLToPath(new URL("../../../", import.meta.url));
const require = nodeModule.createRequire(join(LAB, "package.json"));
const ts = require("typescript") as typeof import("typescript");

type Context = { parentURL?: string; conditions?: string[] };
type Resolved = { url: string; format?: string; shortCircuit?: boolean };
type Loaded = { format: string; source?: string | Uint8Array; shortCircuit?: boolean };
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: {
    resolve(specifier: string, context: Context, next: (specifier: string, context: Context) => Resolved): Resolved;
    load(url: string, context: Context, next: (url: string, context: Context) => Loaded): Loaded;
  }): void;
};

const NOOP = "export const __stub = true;";
const ACTIONS = (names: string[]) => names.map((n) => `export async function ${n}() {}`).join("\n");
/** The seams a view may import, by specifier. A server action is inert: forms render, nothing runs. */
const STUBS: Record<string, string> = {
  "next/link": `import { createElement } from "react";
export default function Link({ href, prefetch, ...props }) { void prefetch; return createElement("a", { href: String(href), ...props }); }`,
  "next/navigation": `export const usePathname = () => globalThis.__operatePath ?? "/overview";
export const useRouter = () => ({ refresh() {}, push() {}, replace() {} });
export function notFound() { throw new Error("NEXT_NOT_FOUND"); }
export function redirect(to) { throw new Error("NEXT_REDIRECT " + to); }`,
  "@/lib/auth/guard": `export const providerAccessForRequest = async () => globalThis.__operateAccess;
export async function requireProviderWorkspace() {
  const a = globalThis.__operateAccess;
  if (a?.kind !== "ready") throw new Error("NEXT_NOT_FOUND");
  return a.workspace;
}`,
  "./api": "export const artifactPort = () => globalThis.__operateArtifacts;",
  "@/lib/auth/actions": ACTIONS(["selectWorkspace"]),
  "@/lib/auth/sign-in": ACTIONS(["signIn", "signOut"]),
  "./sign-in.ts": ACTIONS(["signIn", "signOut"]),
  "@/lib/services/control/actions": ACTIONS(["registerModel", "smokeDeployment", "proposeChange"]),
  "./actions": ACTIONS(["registerRevision", "createProject", "startImport", "createUpload", "grantPart", "completeUpload", "createRevision"]),
  "next/headers": NOOP,
};

function probe(base: string): string | null {
  for (const c of [base, `${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")])
    if (/\.(tsx?|mjs|js)$/.test(c) && existsSync(c)) return c;
  return null;
}

let registered = false;
function register(): void {
  if (registered) return;
  registered = true;
  registerHooks({
    resolve(specifier, context, next) {
      if (Object.hasOwn(STUBS, specifier)) return { url: `operate-stub:${specifier}`, shortCircuit: true };
      if (specifier.endsWith(".module.css")) return { url: `operate-css:${specifier}`, shortCircuit: true };
      if (context.parentURL?.startsWith("operate-stub:")) return next(specifier, { ...context, parentURL: pathToFileURL(join(LAB, "package.json")).href });
      const parent = context.parentURL?.startsWith("file:") ? fileURLToPath(context.parentURL) : null;
      const local = specifier.startsWith("@/") ? probe(join(LAB, specifier.slice(2)))
        : parent && specifier.startsWith(".") && !parent.includes("node_modules") ? probe(join(parent, "..", specifier)) : null;
      return local ? { url: pathToFileURL(local).href, shortCircuit: true } : next(specifier, context);
    },
    load(url, context, next) {
      if (url.startsWith("operate-stub:")) return { format: "module", source: STUBS[url.slice("operate-stub:".length)], shortCircuit: true };
      // A CSS module's class is its own name: the page carries the raw module CSS (operateCss()).
      if (url.startsWith("operate-css:")) return { format: "module", source: "export default new Proxy({}, { get: (_, k) => String(k) });", shortCircuit: true };
      if (url.endsWith(".tsx")) {
        const source = readFileSync(fileURLToPath(url), "utf8");
        const out = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
        return { format: "module", source: out.outputText, shortCircuit: true };
      }
      return next(url, context);
    },
  });
}

/** A Lab module (path from the Lab root), loaded through the stubs. */
export async function load<T>(path: string): Promise<T> {
  register();
  return (await import(pathToFileURL(join(LAB, path)).href)) as T;
}

/** The element as static HTML, with `path` as the current route (the shell's aria-current). */
export async function html(element: ReactElement, path = "/overview"): Promise<string> {
  register();
  (globalThis as { __operatePath?: string }).__operatePath = path;
  const { renderToStaticMarkup } = (await import(require.resolve("react-dom/server"))) as typeof import("react-dom/server");
  return renderToStaticMarkup(element);
}

/** A whole document: the Lab's root class, lab.css and the lane's module CSS (class names unhashed). */
export function page(body: string): string {
  const css = ["app/lab.css", "app/(provider)/operate.module.css"].map((p) => readFileSync(join(LAB, p), "utf8")).join("\n");
  return `<!doctype html><html lang="en" class="lab"><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body>${body}</body></html>`;
}

// The browser: the App install's Playwright, as tests/ux/browser.ts resolves it (no Lab test dependency).
export type Tab = { name: string; tag: string; onScreen: boolean };
export type Shot = {
  setContent(html: string): Promise<void>;
  setViewportSize(size: { width: number; height: number }): Promise<void>;
  evaluate<T>(fn: string): Promise<T>;
  keyboard: { press(key: string): Promise<void> };
  close(): Promise<void>;
};
type Chromium = { launch(o: { headless: boolean }): Promise<{ newPage(o: { viewport: { width: number; height: number } }): Promise<Shot>; close(): Promise<void> }> };

export const VIEWPORTS = [{ width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1440, height: 900 }] as const;

export async function launch() {
  const { appDir } = await import("../browser.ts");
  const { chromium } = nodeModule.createRequire(join(appDir, "package.json"))("@playwright/test") as { chromium: Chromium };
  return chromium.launch({ headless: true });
}

/** Tab `count` times from the top: each focused element's accessible-ish name and whether it is drawn on screen. */
export async function tabWalk(page: Shot, count: number): Promise<Tab[]> {
  await page.evaluate("document.activeElement?.blur(), window.scrollTo(0, 0)");
  const out: Tab[] = [];
  for (let i = 0; i < count; i += 1) {
    await page.keyboard.press("Tab");
    out.push(await page.evaluate(`(() => {
      const e = document.activeElement; if (!e || e === document.body) return { name: "", tag: "body", onScreen: false };
      const r = e.getBoundingClientRect();
      const name = (e.getAttribute("aria-label") || e.textContent || e.getAttribute("name") || "").trim().replace(/\\s+/g, " ");
      return { name, tag: e.tagName.toLowerCase(), onScreen: r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth };
    })()`));
  }
  return out;
}

/** The page scrolls sideways (wider content than the viewport). */
export const overflows = (page: Shot) => page.evaluate<boolean>("document.documentElement.scrollWidth > document.documentElement.clientWidth");
/** The visible text of the page, whitespace-collapsed. */
export const text = (page: Shot) => page.evaluate<string>("document.body.innerText.replace(/\\s+/g, ' ')");

export type Role = "viewer" | "developer" | "administrator";
export type Membership = { providerId: string; providerName: string; role: Role };
export type Access =
  | { kind: "signed-out" } | { kind: "unavailable" } | { kind: "denied" }
  | { kind: "select"; workspaces: Membership[] }
  | { kind: "ready"; workspace: Membership; workspaces: Membership[] };
type Component = (props: Record<string, unknown>) => ReactElement | Promise<ReactElement>;

/** The provider layout around `pageFile` (an app/(provider) page, or null), as `access` sees it at `path`. */
export async function route(pageFile: string | null, access: Access, path: string, query: Record<string, string> = {}): Promise<string> {
  (globalThis as { __operateAccess?: Access }).__operateAccess = access;
  const { default: Layout } = await load<{ default: Component }>("app/(provider)/layout.tsx");
  let body: ReactElement | null = null;
  if (pageFile !== null && access.kind === "ready") {
    const { default: Page } = await load<{ default: Component }>(`app/(provider)/${pageFile}`);
    body = await Page({ searchParams: Promise.resolve(query), params: Promise.resolve({}) });
  }
  return html(await Layout({ children: body }), path);
}
