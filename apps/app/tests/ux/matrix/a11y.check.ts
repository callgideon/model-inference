// UX-11 accessibility pass, App side (07-handoff presentation fixtures: 200% zoom, reduced motion,
// keyboard-only; 02-foundations: "At 200% zoom, primary tasks remain operable", "Respect reduced
// motion"), on the synthetic shell harness (tests/ux/harness: components/sidebar.tsx with fixture
// accounts). a11y-probe.js reads the rendered page: sideways overflow, unnamed controls, AA text
// contrast, movement under prefers-reduced-motion. UXV-A01 is the probe's own failure oracle: each check
// must report a planted defect, so a check that reports nothing is never read as a pass. No screenshots.
// Named .check.ts, not .test.ts: console-test/lab-test run test files in parallel, and a second `next dev`
// on the same harness project stops with "Another next dev server is already running" (measured), so this
// suite runs where nothing else holds the harness: the UX matrix (one part at a time) and its mutant runner.
// ponytail: shares UX-00's harness project; give it a matrix-owned harness dir to rejoin the default glob.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import type { Page } from "@playwright/test";
import { FRAMES, harness, hydrated, type Harness } from "../browser.ts";

const PROBE = readFileSync(resolve(import.meta.dirname, "a11y-probe.js"), "utf8");
type Probe = { overflow: boolean; unnamed: string[]; contrast: string[]; unjudged: string[]; motion: string[] };
const probe = (page: Page) => page.evaluate<Probe>(PROBE);
/** 1280×800 at 200% zoom lays out as 640×400 CSS px (WCAG 1.4.10's reflow arithmetic). */
const ZOOM_200 = { width: 640, height: 400 };
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const PATH = "/usage?email=long&operator=1";
const app = resolve(import.meta.dirname, "../../..");
/** The class list of a component's popup, as committed (the shell harness renders no dialog or menu). */
const classesOf = (file: string, marker: string) => {
  const hit = new RegExp(`"([^"]*${marker}[^"]*)"`).exec(readFileSync(resolve(app, file), "utf8"));
  assert.ok(hit, `${file} still has its ${marker} popup`);
  return hit[1];
};

/** UXV_SCREENS=<dir>: the screenshot index's synthetic shots (harness fixtures only, never E3A); off by default. */
const shot = async (page: Page, name: string) => {
  if (process.env.UXV_SCREENS) await page.screenshot({ path: resolve(process.env.UXV_SCREENS, `${name}.png`) });
};

let h: Harness;
before(async () => {
  h = await harness();
});
after(async () => {
  await h?.stop();
});

async function open(viewport: { width: number; height: number }, reducedMotion: "reduce" | "no-preference" = "reduce"): Promise<Page> {
  const page = await h.browser.newPage({ viewport, reducedMotion });
  await page.goto(`${h.url}${PATH}`);
  await hydrated(page, "#console-main");
  return page;
}

/** Each Tab stop: its name, drawn inside the viewport, the topmost element at its centre, and an outline or ring. */
async function tabWalk(page: Page, max = 40) {
  const stops: { name: string; inView: boolean; uncovered: boolean; indicated: boolean }[] = [];
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press("Tab");
    await page.evaluate(FRAMES);
    const stop = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      // The end of the page's order: the body, or next dev's own overlay (a development tool, not the App).
      if (el === null || el === document.body || el.tagName === "NEXTJS-PORTAL") return null;
      const r = el.getBoundingClientRect();
      // WCAG 2.4.11: the focused element is not entirely hidden - judged on its part inside the viewport
      // (a tall control at 200% zoom may be scrolled to only in part).
      const [x0, x1, y0, y1] = [Math.max(r.left, 0), Math.min(r.right, innerWidth), Math.max(r.top, 0), Math.min(r.bottom, innerHeight)];
      const top = x1 > x0 && y1 > y0 ? document.elementFromPoint((x0 + x1) / 2, (y0 + y1) / 2) : null;
      const s = getComputedStyle(el);
      return {
        name: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 40) || el.tagName,
        inView: x1 > x0 && y1 > y0,
        uncovered: top !== null && (top === el || el.contains(top) || top.contains(el)),
        indicated: (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none",
      };
    });
    if (stop === null || (stops.length > 0 && stop.name === stops[0].name)) break;
    stops.push(stop);
  }
  return stops;
}

test("UXV-A01 the probe reports each planted defect: low-contrast text, an unnamed button, a sideways overflow, a sliding, a slid and a moving box; an inactive control, a hidden spinner and a fade are exempt", async () => {
  const page = await open(DESKTOP, "no-preference"); // planted motion is motion whatever the setting
  await page.evaluate(() => {
    const main = document.querySelector("#console-main")!;
    main.insertAdjacentHTML(
      "beforeend",
      `<style>@keyframes uxv-slide { from { transform: translateX(0) } to { transform: translateX(40px) } } @keyframes uxv-spin { to { rotate: 1turn } } @keyframes uxv-fade { from { opacity: 0 } to { opacity: 1 } }</style>
       <div id="uxv-done" style="animation:uxv-slide 30ms 1;width:20px;height:20px">d</div>
       <div id="uxv-move" style="transition:transform 3s linear;width:20px;height:20px">m</div>
       <div id="uxv-fade" style="animation:uxv-fade 2s infinite;width:20px;height:20px">f</div>
       <p id="uxv-faint" style="color:#c8c8c8;background:#fff">faint synthetic text</p>
       <button id="uxv-blank" type="button" style="width:24px;height:24px"></button>
       <button disabled style="color:#c8c8c8;background:#fff">inactive synthetic</button>
       <div id="uxv-slide" style="animation:uxv-slide 2s infinite;width:20px;height:20px">s</div>
       <span aria-hidden="true" style="display:inline-block;animation:uxv-spin 1s infinite">o</span>
       <div id="uxv-wide" style="width:4000px;height:1px"></div>`,
    );
  });
  await new Promise((done) => setTimeout(done, 200)); // #uxv-done's 30 ms slide has finished: only declared
  await page.evaluate(() => {
    (document.querySelector("#uxv-move") as HTMLElement).style.transform = "translateX(40px)"; // a running transition
  });
  const r = await probe(page);
  assert.equal(r.overflow, true, "a 4000 px element scrolls the page sideways");
  assert.ok(r.motion.some((x) => x.includes("#uxv-done") && x.includes("declares")), `a finished slide is still declared motion: ${r.motion}`);
  assert.ok(r.motion.some((x) => x.includes("#uxv-move") && x.includes("runs")), `a running transform transition: ${r.motion}`);
  assert.ok(!r.motion.some((x) => x.includes("#uxv-fade")), "an opacity fade is not motion");
  assert.ok(r.unnamed.some((x) => x.includes("#uxv-blank")), `the unnamed button: ${r.unnamed}`);
  assert.ok(r.contrast.some((x) => x.includes("faint synthetic text")), `the faint text: ${r.contrast}`);
  assert.ok(!r.contrast.some((x) => x.includes("inactive synthetic")), "a disabled control is exempt");
  assert.ok(r.motion.some((x) => x.includes("#uxv-slide")), `the sliding box: ${r.motion}`);
  assert.equal(r.motion.filter((x) => x.includes('"o"')).length, 0, "an aria-hidden pending indicator is exempt");
  await page.close();
});

test("UXV-A02 at 200% zoom (640×400 CSS px) the shell never scrolls sideways; Tab reaches every control in view, uncovered and with a focus indicator; the menu opens and closes by keyboard", async () => {
  const page = await open(ZOOM_200);
  assert.equal((await probe(page)).overflow, false, "no sideways scroll at 200%");
  await shot(page, "app-shell-200pct");
  const stops = await tabWalk(page);
  assert.ok(stops.some((s) => s.name === "Open menu"), `Tab reaches the menu: ${stops.map((s) => s.name)}`);
  for (const s of stops) assert.deepEqual([s.name, s.inView, s.uncovered, s.indicated], [s.name, true, true, true], `stop "${s.name}"`);
  await page.getByRole("button", { name: "Open menu" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "Navigation" }).waitFor({ state: "visible" });
  assert.equal((await probe(page)).overflow, false, "the open menu fits at 200%");
  await shot(page, "app-menu-200pct");
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "Navigation" }).waitFor({ state: "detached" }).catch(() => undefined);
  assert.equal(await page.getByRole("dialog", { name: "Navigation" }).count(), 0, "Escape closes it");
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.textContent), "Open menu", "focus returns to the menu button");
  await page.close();
});

test("UXV-A03 every control is named and all text meets AA contrast, at 1440 px and with the 390 px menu open", async () => {
  const desktop = await open(DESKTOP);
  const d = await probe(desktop);
  assert.deepEqual([d.unnamed, d.contrast, d.unjudged], [[], [], []], "desktop shell");
  await shot(desktop, "app-shell-1440");
  await desktop.close();
  const mobile = await open(MOBILE);
  await mobile.getByRole("button", { name: "Open menu" }).click();
  await mobile.getByRole("dialog", { name: "Navigation" }).waitFor({ state: "visible" });
  const m = await probe(mobile);
  assert.deepEqual([m.unnamed, m.contrast, m.unjudged], [[], [], []], "the open mobile menu");
  await shot(mobile, "app-menu-390");
  await mobile.close();
});

test("UXV-A04 reduced motion: opening and closing the mobile menu moves nothing", async () => {
  const page = await open(MOBILE);
  await page.getByRole("button", { name: "Open menu" }).click();
  const opening = await probe(page);
  await page.getByRole("dialog", { name: "Navigation" }).waitFor({ state: "visible" });
  await page.keyboard.press("Escape");
  const closing = await probe(page);
  assert.deepEqual([opening.motion, closing.motion], [[], []]);
  await page.close();
});

// WR-UXVF-3: the App has no prefers-reduced-motion rule; its dialog and menus zoom/slide in (tw-animate)
// whatever the setting. Reported FAIL in the acceptance report; this case passes once the WR lands, and
// its todo comes off then (a todo keeps console-test green while the defect is open, never hides it).
test("UXV-A05 reduced motion: the App's dialog and dropdown menu popups, as committed, move nothing", { todo: "FAIL[WR-UXVF-3]: no prefers-reduced-motion rule in app/globals.css" }, async () => {
  const page = await open(DESKTOP);
  const popups = [classesOf("components/ui/dialog.tsx", "zoom-in-95"), classesOf("components/ui/dropdown-menu.tsx", "slide-in-from-top-2")];
  await page.evaluate((list) => {
    for (const c of list) document.querySelector("#console-main")!.insertAdjacentHTML("beforeend", `<div data-open="" data-side="bottom" class="${c}" style="position:static;transform:none">synthetic popup</div>`);
  }, popups);
  assert.deepEqual((await probe(page)).motion, []);
  await page.close();
});
