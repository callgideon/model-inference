// node --test "tests/**/*.test.ts"
//
// UX-T03, fixture half (UX-04 exit): the real first-call panel, rendered with the App's CSS in Chromium
// at 1280x720 inside the console shell's layout. A brand-new fixture account sees an enabled Create key
// above the fold; an account with an active key sees it offered and no Create key button at all.
//
// Oracles: a guide that moves below the fold (header, panel order or step order), drops the button for
// a new account, or pushes an existing-key account to create another key fails here. Synthetic data only;
// nothing is captured.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { chromium, type Browser } from "@playwright/test";
import { keysPageModel } from "../../../app/(console)/api-keys/view-model.ts";
import { firstCallModel } from "../../../app/(console)/models/first-call.ts";
import { parsePublishedModel } from "../../../lib/contracts/v2/published-model.ts";
import { appCss, renderFirstCall } from "./render.ts";

const PUBLISHED = new URL("../../../../infrx-api/infrx/contracts/v2/published/published_marlin_credit.json", import.meta.url);
const MODEL = parsePublishedModel(JSON.parse(readFileSync(PUBLISHED, "utf8")));
const READY = { state: "ready", account: { suspended: false } };
const KEY = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Robotics evaluation",
  prefix: "sk-infrx-AbCd",
  created_at: "2026-09-30T10:00:00Z",
  last_used_at: null,
  revoked_at: null,
  trace_mode: null,
};
const empty = { ok: true as const, value: { items: [], next_cursor: null } };

let browser: Browser;
let css: string;
test.before(async () => {
  css = await appCss();
  try {
    browser = await chromium.launch();
  } catch (error) {
    throw new Error(`Chromium for Playwright is not installed: run \`pnpm exec playwright install chromium\` in apps/app (${(error as Error).message.split("\n")[0]})`);
  }
});
test.after(async () => browser?.close());

async function open(keys: (typeof KEY)[]) {
  const guide = firstCallModel(keysPageModel(READY, { ok: true, value: keys }), empty);
  const body = await renderFirstCall({ guide, model: MODEL, baseUrl: "https://api.example.test" });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.setContent(`<!doctype html><html><head><style>${css}</style></head><body>${body}</body></html>`);
  return page;
}

test("a brand-new account sees Create key above the fold at 1280x720", async () => {
  const page = await open([]);
  try {
    assert.equal(await page.getByRole("heading", { level: 2, name: "Make your first request" }).count(), 1);
    const button = page.getByRole("button", { name: "Create key" });
    assert.equal(await button.count(), 1, "no Create key button for a new account");
    assert.ok(await button.isEnabled(), "Create key is disabled");
    const box = (await button.boundingBox())!;
    assert.ok(box !== null && box.y >= 0 && box.y + box.height <= 720, `Create key sits at y=${box?.y}..${(box?.y ?? 0) + (box?.height ?? 0)}, below the fold`);
  } finally {
    await page.close();
  }
});

test("an account with an active key is offered it and is not pushed to create another", async () => {
  const page = await open([KEY]);
  try {
    assert.equal(await page.getByRole("button", { name: "Create key" }).count(), 0, "an existing-key account is pushed to create another");
    const offer = page.getByText("Use an existing key or create a new one.");
    const box = (await offer.boundingBox())!;
    assert.ok(box !== null && box.y + box.height <= 720, "the existing-key step is below the fold");
    assert.equal(await page.getByText(KEY.prefix).count(), 1, "the key is not listed by name and prefix");
    // The prefix is metadata only: no code block on the page carries it, and each reads INFRX_API_KEY.
    for (const code of await page.locator("pre").allTextContents()) {
      assert.ok(!code.includes(KEY.prefix) && !code.includes("…"), "a code block carries the key prefix");
      assert.match(code, /INFRX_API_KEY/);
    }
  } finally {
    await page.close();
  }
});
