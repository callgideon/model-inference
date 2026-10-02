// UX-00: the synthetic browser harness. It serves tests/ux/harness (a separate Next project that
// renders the Lab's own root layout and primitives with synthetic records) with `next dev` on a free
// loopback port, and drives it with Chromium. Nothing in app/, lib/ or components/ imports it
// (UX00-S03), so a production build never ships a fixture. No screenshots, traces or videos.
//
// Playwright is the project's browser setup (apps/app devDependency @playwright/test 1.63); the Lab
// adds no test dependency (UX-00 may add Base UI and Lucide only), so it resolves the App's install.
// ponytail: borrowed from the App install; give the Lab its own @playwright/test if the apps split CI.
import { spawn, type ChildProcess } from "node:child_process";
import { realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { resolve } from "node:path";

const lab = resolve(import.meta.dirname, "../..");
/** The App's directory, found through node_modules (a mutant runner's copy links it to the real Lab install). */
export const appDir = resolve(realpathSync(resolve(lab, "node_modules")), "../../app");
const appInstall = resolve(appDir, "package.json");

// The slice of Playwright's API these tests use (the Lab's tsc cannot see the App's install).
export type Page = {
  goto(url: string): Promise<unknown>;
  close(): Promise<void>;
  addInitScript(script: string): Promise<void>;
  keyboard: { press(key: string): Promise<void> };
  evaluate<T>(fn: string): Promise<T>;
  waitForFunction(fn: string, arg?: unknown, options?: { timeout?: number }): Promise<unknown>;
  getByRole(role: string, options: { name: string | RegExp; exact?: boolean }): Locator;
  getByText(text: string | RegExp, options?: { exact?: boolean }): Locator;
  locator(selector: string): Locator;
};
export type Locator = {
  click(): Promise<void>;
  focus(): Promise<void>;
  count(): Promise<number>;
  getAttribute(name: string): Promise<string | null>;
  textContent(): Promise<string | null>;
  waitFor(options?: { state?: "attached" | "detached" | "visible" | "hidden"; timeout?: number }): Promise<void>;
  isDisabled(): Promise<boolean>;
  first(): Locator;
};
export type Browser = {
  newPage(options: { viewport: { width: number; height: number }; reducedMotion?: "reduce" }): Promise<Page>;
  close(): Promise<void>;
};
type Chromium = { launch(options: { headless: boolean }): Promise<Browser> };

function chromium(): Chromium {
  try {
    return (createRequire(appInstall)("@playwright/test") as { chromium: Chromium }).chromium;
  } catch {
    throw new Error("UX harness: @playwright/test is missing: cd apps/app && pnpm install --frozen-lockfile");
  }
}

function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const server = createServer();
    server.once("error", fail);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as { port: number };
      server.close(() => done(port));
    });
  });
}

export type Harness = { url: string; browser: Browser; stop: () => Promise<void> };

/** `next dev` on a harness project (UX-00's by default) and one headless Chromium; `stop` ends both. */
export async function harness(dir = resolve(import.meta.dirname, "harness")): Promise<Harness> {
  const port = await freePort();
  const next = resolve(lab, "node_modules/next/dist/bin/next");
  const server: ChildProcess = spawn(process.execPath, [next, "dev", dir, "-H", "127.0.0.1", "-p", String(port)], {
    cwd: lab,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      NODE_ENV: "development",
      NEXT_TELEMETRY_DISABLED: "1",
      // Synthetic, unreachable: nothing in the harness talks to a backend.
      NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:9",
      NEXT_PUBLIC_SUPABASE_ANON_KEY: "synthetic-fixture",
      NEXT_PUBLIC_LAB_URL: `http://127.0.0.1:${port}`,
    },
  });
  let log = "";
  server.stdout?.on("data", (c) => (log += c));
  server.stderr?.on("data", (c) => (log += c));
  const url = `http://127.0.0.1:${port}`;
  const stopServer = () => {
    try {
      process.kill(-server.pid!, "SIGKILL");
    } catch {
      // already gone
    }
  };
  const deadline = Date.now() + 90_000;
  for (;;) {
    if (server.exitCode !== null) throw new Error(`UX harness: next dev exited\n${log}`);
    try {
      if ((await fetch(`${url}/`)).status === 200) break;
    } catch {
      // not listening yet
    }
    if (Date.now() > deadline) {
      stopServer();
      throw new Error(`UX harness: next dev did not serve / within 90 s\n${log}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  let browser: Browser;
  try {
    browser = await chromium().launch({ headless: true });
  } catch (error) {
    stopServer();
    throw error;
  }
  return {
    url,
    browser,
    stop: async () => {
      await browser.close();
      stopServer();
    },
  };
}
