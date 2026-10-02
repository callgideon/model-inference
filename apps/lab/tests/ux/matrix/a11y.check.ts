// UX-11 accessibility pass, Lab side (07-handoff presentation fixtures: 200% zoom, reduced motion,
// keyboard-only), on the Lab's synthetic harnesses: UX-00's primitives page (tests/ux/harness: page
// header, every service state, a field error, long ids, the dialog and the drawer) and UX-06's import
// wizard (tests/ux/improve/harness). The probe is the App suite's a11y-probe.js, read as text (no import
// crosses the apps; UXV-A01 there plants a defect for each of its checks). No screenshots.
// Named .check.ts, not .test.ts: console-test/lab-test run test files in parallel, and a second `next dev`
// on the same harness project stops with "Another next dev server is already running" (measured), so this
// suite runs where nothing else holds the harness: the UX matrix (one part at a time) and its mutant runner.
// ponytail: shares UX-00's harness project; give it a matrix-owned harness dir to rejoin the default glob.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import { appDir, harness, type Harness, type Page } from "../browser.ts";

const PROBE = readFileSync(resolve(appDir, "tests/ux/matrix/a11y-probe.js"), "utf8");
type Probe = { overflow: boolean; unnamed: string[]; contrast: string[]; unjudged: string[]; motion: string[] };
const probe = (page: Page) => page.evaluate<Probe>(PROBE);
const FRAMES = "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))";
/** 1280×800 at 200% zoom lays out as 640×400 CSS px (WCAG 1.4.10's reflow arithmetic). */
const ZOOM_200 = { width: 640, height: 400 };
const DESKTOP = { width: 1440, height: 900 };

/** UXV_SCREENS=<dir>: the screenshot index's synthetic shots (harness fixtures only); off by default. */
const shot = async (page: Page, name: string) => {
  if (process.env.UXV_SCREENS) await (page as Page & { screenshot(o: { path: string }): Promise<unknown> }).screenshot({ path: resolve(process.env.UXV_SCREENS, `${name}.png`) });
};

let primitives: Harness;
let wizard: Harness;
before(async () => {
  [primitives, wizard] = await Promise.all([harness(), harness(resolve(import.meta.dirname, "../improve/harness"))]);
});
after(async () => {
  await Promise.all([primitives?.stop(), wizard?.stop()]);
});

const PAGES = {
  primitives: { h: () => primitives, ready: (p: Page) => p.getByRole("heading", { name: "Primitives" }).waitFor() },
  wizard: { h: () => wizard, ready: (p: Page) => p.getByRole("button", { name: "Preview mapping" }).waitFor() },
};
async function open(which: keyof typeof PAGES, viewport: { width: number; height: number }): Promise<Page> {
  const page = await PAGES[which].h().browser.newPage({ viewport, reducedMotion: "reduce" });
  await page.goto(PAGES[which].h().url);
  await PAGES[which].ready(page);
  await page.waitForFunction(`Object.keys(document.querySelector("button")).some((k) => k.startsWith("__reactFiber"))`, undefined, { timeout: 30_000 });
  await page.evaluate(FRAMES);
  return page;
}

const STOP = `(() => {
  const el = document.activeElement;
  if (el === null || el === document.body || el.tagName === "NEXTJS-PORTAL") return null;
  const r = el.getBoundingClientRect();
  // WCAG 2.4.11: the focused element is not entirely hidden - judged on its part inside the viewport
  // (a tall control at 200% zoom, like the import spec, may be scrolled to only in part).
  const [x0, x1, y0, y1] = [Math.max(r.left, 0), Math.min(r.right, innerWidth), Math.max(r.top, 0), Math.min(r.bottom, innerHeight)];
  const top = x1 > x0 && y1 > y0 ? document.elementFromPoint((x0 + x1) / 2, (y0 + y1) / 2) : null;
  const s = getComputedStyle(el);
  return {
    name: ((el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 40)) || el.getAttribute("name") || el.tagName,
    inView: x1 > x0 && y1 > y0,
    uncovered: top !== null && (top === el || el.contains(top) || top.contains(el)),
    indicated: (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none",
  };
})()`;
type Stop = { name: string; inView: boolean; uncovered: boolean; indicated: boolean };
async function tabWalk(page: Page, max = 60): Promise<Stop[]> {
  const stops: Stop[] = [];
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press("Tab");
    await page.evaluate(FRAMES);
    const stop = await page.evaluate<Stop | null>(STOP);
    if (stop === null || (stops.length > 0 && stop.name === stops[0].name)) break;
    stops.push(stop);
  }
  return stops;
}

test("UXV-L01 at 200% zoom (640×400 CSS px) neither page scrolls sideways, and every Tab stop is drawn in view, uncovered, with a focus indicator", async () => {
  for (const which of ["primitives", "wizard"] as const) {
    const page = await open(which, ZOOM_200);
    assert.equal((await probe(page)).overflow, false, `${which}: no sideways scroll at 200%`);
    await shot(page, `lab-${which}-200pct`);
    const stops = await tabWalk(page);
    assert.ok(stops.length >= 3, `${which}: Tab reaches the page's controls: ${stops.map((s) => s.name)}`);
    for (const s of stops) assert.deepEqual([s.inView, s.uncovered, s.indicated], [true, true, true], `${which} stop "${s.name}" in view, uncovered, indicated`);
    await page.close();
  }
});

test("UXV-L02 at 200% zoom the dialog and the drawer open by keyboard, fit, and close back to their trigger", async () => {
  const page = await open("primitives", ZOOM_200);
  for (const [trigger, title] of [["Open dialog", "Synthetic dialog"], ["Open drawer", "Synthetic drawer"]]) {
    await page.getByRole("button", { name: trigger, exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("dialog", { name: title }).waitFor({ state: "visible" });
    assert.equal((await probe(page)).overflow, false, `${title} fits at 200%`);
    await page.keyboard.press("Escape");
    await page.getByRole("dialog", { name: title }).waitFor({ state: "detached" }).catch(() => undefined);
    await page.evaluate(FRAMES);
    assert.equal(await page.evaluate<string>(`document.activeElement?.textContent?.trim() ?? ""`), trigger, `focus returns to ${trigger}`);
  }
  await page.close();
});

test("UXV-L03 every control is named and all active text meets AA contrast, on both pages and inside the open dialog and drawer", async () => {
  for (const which of ["primitives", "wizard"] as const) {
    const page = await open(which, DESKTOP);
    const r = await probe(page);
    assert.deepEqual([r.unnamed, r.contrast, r.unjudged], [[], [], []], which);
    await shot(page, `lab-${which}-1440`);
    await page.close();
  }
  const page = await open("primitives", DESKTOP);
  for (const [trigger, title] of [["Open dialog", "Synthetic dialog"], ["Open drawer", "Synthetic drawer"]]) {
    await page.getByRole("button", { name: trigger, exact: true }).click();
    await page.getByRole("dialog", { name: title }).waitFor({ state: "visible" });
    const r = await probe(page);
    assert.deepEqual([r.unnamed, r.contrast], [[], []], title);
    await shot(page, `lab-${title.toLowerCase().replace(/ /g, "-")}-1440`);
    await page.keyboard.press("Escape");
    await page.getByRole("dialog", { name: title }).waitFor({ state: "detached" }).catch(() => undefined);
  }
  await page.close();
});

test("UXV-L04 reduced motion: the pending spinner stands still, and opening the dialog or the drawer moves nothing", async () => {
  const page = await open("primitives", DESKTOP);
  assert.equal(await page.evaluate<string>(`getComputedStyle(document.querySelector(".lab-spin")).animationName`), "none", "the pending button's spinner does not spin");
  assert.deepEqual((await probe(page)).motion, [], "the page at rest");
  for (const [trigger, title] of [["Open dialog", "Synthetic dialog"], ["Open drawer", "Synthetic drawer"]]) {
    await page.getByRole("button", { name: trigger, exact: true }).click();
    assert.deepEqual((await probe(page)).motion, [], `${title} opening`);
    await page.getByRole("dialog", { name: title }).waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    assert.deepEqual((await probe(page)).motion, [], `${title} closing`);
    await page.getByRole("dialog", { name: title }).waitFor({ state: "detached" }).catch(() => undefined);
  }
  await page.close();
});
