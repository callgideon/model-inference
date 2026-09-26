/**
 * E3A: the browser journey's Playwright config. Only `tests/integration/app/runner.py` runs it: it
 * sets the App, control and gateway URLs (loopback, e4b block; `target.ts` refuses anything else)
 * and the output directory. No screenshots, videos or traces are kept (no media in evidence).
 */
import { defineConfig } from "@playwright/test";
import { loopback } from "./target.ts";

const app = loopback("E3A_APP_URL", process.env);
const out = process.env.E3A_OUT;
if (!out) throw new Error("E3A_OUT is not set: run tests/integration/app/runner.py");

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.ts$/,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 20_000 },
  outputDir: `${out}/playwright-artifacts`,
  reporter: [["json", { outputFile: `${out}/playwright.json` }], ["list"]],
  use: {
    baseURL: app.origin,
    headless: true,
    screenshot: "off",
    video: "off",
    trace: "off",
    navigationTimeout: 60_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
