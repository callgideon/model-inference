// UX-00: the App's synthetic browser harness. It serves tests/ux/harness (a separate Next project that
// renders the App's own root layout and components with synthetic fixtures) with `next dev` on a free
// loopback port and drives it with the project's Playwright Chromium. Nothing under app/, lib/ or
// components/ imports it (UXA-S02), so no production build ships a fixture. It never takes
// screenshots, traces or videos, and it is separate from the real E3A suite (tests/e2e), whose capture
// policy stays off.
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { chromium, type Browser, type Page } from "@playwright/test";

const app = resolve(import.meta.dirname, "../..");

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

/** `next dev` on the harness project and one headless Chromium; `stop` ends both. */
export async function harness(): Promise<Harness> {
  const port = await freePort();
  const next = resolve(app, "node_modules/next/dist/bin/next");
  const server = spawn(process.execPath, [next, "dev", resolve(import.meta.dirname, "harness"), "-H", "127.0.0.1", "-p", String(port)], {
    cwd: app,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      NODE_ENV: "development",
      NEXT_TELEMETRY_DISABLED: "1",
      INFRX_UX_APP_DIR: app,
      // Synthetic and unreachable: the App's instrumentation requires them; nothing here calls them.
      NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:9",
      NEXT_PUBLIC_SUPABASE_ANON_KEY: "synthetic-fixture",
    },
  });
  let log = "";
  server.stdout.on("data", (c) => (log += c));
  server.stderr.on("data", (c) => (log += c));
  const stopServer = () => {
    try {
      process.kill(-server.pid!, "SIGKILL");
    } catch {
      // already gone
    }
  };
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 90_000;
  for (;;) {
    if (server.exitCode !== null) throw new Error(`UX harness: next dev exited\n${log}`);
    try {
      if ((await fetch(`${url}/models`)).status === 200) break;
    } catch {
      // not listening yet
    }
    if (Date.now() > deadline) {
      stopServer();
      throw new Error(`UX harness: next dev did not serve within 90 s\n${log}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  let browser: Browser;
  try {
    browser = await chromium.launch({ headless: true });
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

/** Two animation frames: what a person's next key press waits at least (Base UI hands focus on in a frame). */
export const FRAMES = "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))";

/** The page once React has hydrated `selector` and a frame has let effects run. */
export async function hydrated(page: Page, selector: string): Promise<void> {
  await page.waitForFunction(
    (s) => {
      const el = document.querySelector(s);
      return el !== null && Object.keys(el).some((k) => k.startsWith("__reactFiber"));
    },
    selector,
    { timeout: 30_000 },
  );
  await page.evaluate(FRAMES);
}
