// UX-06/UX-09: serves this lane's synthetic harness (tests/ux/improve/harness) with `next dev` and one
// headless Chromium, as tests/ux/browser.ts does for UX-00's. No screenshots, traces or videos.
// ponytail: tests/ux/browser.ts's harness() with another directory; fold into it once it takes a `dir`
// (wiring request WR-UXI-2), then delete this file.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { appDir, type Browser, type Harness } from "../browser.ts";

const lab = resolve(import.meta.dirname, "../../..");

const freePort = () =>
  new Promise<number>((done, fail) => {
    const server = createServer();
    server.once("error", fail);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as { port: number };
      server.close(() => done(port));
    });
  });

export async function harness(): Promise<Harness> {
  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const server = spawn(process.execPath, [resolve(lab, "node_modules/next/dist/bin/next"), "dev", resolve(import.meta.dirname, "harness"), "-H", "127.0.0.1", "-p", String(port)], {
    cwd: lab,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:9", NEXT_PUBLIC_SUPABASE_ANON_KEY: "synthetic-fixture", NEXT_PUBLIC_LAB_URL: url },
  });
  let log = "";
  server.stdout?.on("data", (c) => (log += c));
  server.stderr?.on("data", (c) => (log += c));
  const stop = () => {
    try {
      process.kill(-server.pid!, "SIGKILL");
    } catch {
      // already gone
    }
  };
  for (const deadline = Date.now() + 90_000; ; await new Promise((r) => setTimeout(r, 250))) {
    if (server.exitCode !== null) throw new Error(`improve harness: next dev exited\n${log}`);
    if (Date.now() > deadline) throw (stop(), new Error(`improve harness: next dev did not serve / within 90 s\n${log}`));
    if ((await fetch(`${url}/`).then((r) => r.status, () => 0)) === 200) break;
  }
  let browser: Browser;
  try {
    const { chromium } = createRequire(resolve(appDir, "package.json"))("@playwright/test") as { chromium: { launch(o: { headless: boolean }): Promise<Browser> } };
    browser = await chromium.launch({ headless: true });
  } catch (error) {
    stop();
    throw error;
  }
  return { url, browser, stop: async () => (await browser.close(), stop()) };
}
