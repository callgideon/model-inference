// UX-02 (UX-T02, audit C9 / UX-A02): the console navigation by keyboard in a real browser, on the
// synthetic harness (tests/ux/harness renders components/sidebar.tsx with fixture accounts). C9 as
// reproduced live at 390px: Escape left the menu open, and with the menu closed Tab reached the
// offscreen logo link at x=-224. Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import type { Page } from "@playwright/test";
import { FRAMES, harness, hydrated, type Harness } from "../browser.ts";

const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

let h: Harness;
before(async () => {
  h = await harness();
});
after(async () => {
  await h?.stop();
});

async function open(path: string, viewport: { width: number; height: number }): Promise<Page> {
  const page = await h.browser.newPage({ viewport, reducedMotion: "reduce" });
  await page.goto(`${h.url}${path}`);
  await hydrated(page, "#console-main");
  return page;
}

/** Whether `count` of `selector` reaches `want` within 3 s (an animation may finish first): a boolean to assert. */
async function settles(page: Page, selector: string, want: number): Promise<boolean> {
  for (let i = 0; i < 30; i += 1) {
    if ((await page.locator(selector).count()) === want) return true;
    await page.evaluate(FRAMES);
    await new Promise((r) => setTimeout(r, 100));
  }
  return false;
}

async function openMenu(page: Page) {
  await page.getByRole("button", { name: "Open menu" }).click();
  assert.equal(await settles(page, "[role=dialog]", 1), true, "the menu opens as a dialog");
  assert.equal(await page.getByRole("dialog", { name: "Navigation" }).count(), 1, "the dialog is named Navigation");
}

async function press(page: Page, key: string) {
  await page.keyboard.press(key);
  await page.evaluate(FRAMES);
}

/** Where focus is: its accessible text, whether it is drawn inside the viewport, and inside a dialog. */
const focused = (page: Page) =>
  page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (el === null || el === document.body) return { text: "", visible: false, inDialog: false, inMain: false };
    const r = el.getBoundingClientRect();
    const visible = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= window.innerWidth && r.bottom <= window.innerHeight && el.checkVisibility();
    return {
      text: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim(),
      visible,
      inDialog: el.closest("[role=dialog]") !== null,
      inMain: el.closest("main") !== null,
    };
  });

test("UXN-01 at 390px with the menu closed, Tab never reaches an offscreen or hidden link", async () => {
  const page = await open("/usage", MOBILE);
  const seen: string[] = [];
  // From the top of the document to the page's own control (past it is the browser's, or Next's dev overlay).
  for (let i = 0; i < 8 && !seen.includes("Page control"); i += 1) {
    await press(page, "Tab");
    const f = await focused(page);
    seen.push(f.text);
    assert.ok(f.visible, `Tab ${i + 1} focused "${f.text}", which is not drawn on screen (seen: ${seen.join(" > ")})`);
  }
  assert.ok(seen.includes("Open menu"), "the menu trigger is in the tab order");
  assert.ok(seen.includes("Page control"), "Tab reaches the page itself, past no hidden navigation");
  await page.close();
});

test("UXN-02 the open menu is a modal: focus moves in, Tab cannot reach the page, Escape closes and returns focus", async () => {
  const page = await open("/usage", MOBILE);
  await page.getByRole("button", { name: "Open menu" }).focus();
  await press(page, "Enter");
  assert.equal(await settles(page, "[role=dialog]", 1), true, "Enter on the trigger opens the menu");
  assert.equal((await focused(page)).inDialog, true, "focus moved into the menu");
  for (let i = 0; i < 12; i += 1) {
    await press(page, "Tab");
    const f = await focused(page);
    assert.ok(f.inDialog && !f.inMain, `Tab ${i + 1} left the menu for "${f.text}"`);
  }
  await press(page, "Escape");
  assert.equal(await settles(page, "[role=dialog]", 0), true, "Escape closes the menu");
  assert.equal((await focused(page)).text, "Open menu", "Escape returns focus to the trigger");
  await page.close();
});

test("UXN-03 a route change closes the menu: following its link (the current page's too), and going back", async () => {
  const page = await open("/models", MOBILE);
  await openMenu(page);
  await page.getByRole("dialog", { name: "Navigation" }).getByRole("link", { name: "Usage", exact: true }).click();
  await page.waitForURL(/\/usage$/);
  assert.equal(await settles(page, "[role=dialog]", 0), true, "following a link closed the menu");
  await openMenu(page);
  await page.getByRole("dialog", { name: "Navigation" }).getByRole("link", { name: "Usage", exact: true }).click();
  assert.equal(await settles(page, "[role=dialog]", 0), true, "following the link to the current page closed the menu");
  await openMenu(page);
  await page.goBack();
  await page.waitForURL(/\/models$/);
  assert.equal(await settles(page, "[role=dialog]", 0), true, "the back navigation closed the menu");
  await page.close();
});

test("UXN-04 growing to desktop closes the open menu and leaves the persistent navigation usable", async () => {
  const page = await open("/models", MOBILE);
  await openMenu(page);
  await page.setViewportSize(DESKTOP);
  assert.equal(await settles(page, "[role=dialog]", 0), true, "the menu closed at desktop width");
  const nav = page.getByRole("navigation", { name: "Console" });
  assert.equal(await nav.getByRole("link", { name: "Usage", exact: true }).isVisible(), true);
  assert.equal(await page.getByRole("button", { name: "Open menu" }).isVisible(), false, "no menu trigger on desktop");
  await page.close();
});

test("UXN-05 desktop: a skip link first, the nav in the agreed order, aria-current on the page, Docs internal", async () => {
  const page = await open("/usage", DESKTOP);
  await press(page, "Tab");
  assert.equal((await focused(page)).text, "Skip to content", "the first stop skips the navigation");
  assert.equal((await focused(page)).visible, true, "the skip link is visible when focused");
  await press(page, "Enter");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "console-main", "the skip link lands on the page");
  const nav = page.getByRole("navigation", { name: "Console" });
  const labels = (await nav.getByRole("link").allTextContents()).map((t) => t.trim());
  assert.deepEqual(labels, ["Models", "API keys", "Usage", "Credits", "Docs", "Settings"]);
  const current = await nav.locator("[aria-current=page]").allTextContents();
  assert.deepEqual(current.map((t) => t.trim()), ["Usage"], "exactly the current page is marked");
  const docs = nav.getByRole("link", { name: "Docs", exact: true });
  assert.equal(await docs.getAttribute("href"), "/docs");
  assert.equal(await docs.locator(".lucide-external-link").count(), 0, "Docs is internal: no external-link icon");
  await page.close();
});

test("UXN-06 a long email, an unreadable balance and the operator entry fit the menu and the sidebar without overflow", async () => {
  for (const viewport of [MOBILE, DESKTOP]) {
    const page = await open("/models?email=long&balance=unavailable&operator=1", viewport);
    if (viewport === MOBILE) await openMenu(page);
    const scope = viewport === MOBILE ? page.getByRole("dialog", { name: "Navigation" }) : page.locator("aside");
    assert.equal(await scope.getByRole("link", { name: "Operator", exact: true }).count(), 1, "the authorized operator entry stays");
    assert.equal(await scope.getByRole("status").count(), 1, "the unavailable balance is stated as such, not as a figure");
    assert.match((await scope.getByRole("status").textContent()) ?? "", /\S/);
    assert.equal(await scope.getByRole("button", { name: /a-very-long-synthetic-address/ }).count(), 1, "the account menu stays");
    const [scroll, width] = await scope.evaluate((el) => [el.scrollWidth, el.clientWidth]);
    assert.ok(scroll <= width, `${viewport.width}px: content ${scroll}px wider than ${width}px`);
    const [page_scroll, page_width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    assert.ok(page_scroll <= page_width, `${viewport.width}px: the page scrolls sideways`);
    await page.close();
  }
});
