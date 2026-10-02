// UX-00 (UX-JOURNEY foundations): the Lab's scoped stylesheet, its tokens, fixture isolation and the
// primitives' keyboard and state contracts in a real browser at desktop and mobile widths. Every page
// is the synthetic harness (tests/ux/harness); no screenshot is taken. Each case names what it catches.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { after, before, test } from "node:test";
import { appDir, harness, type Harness, type Page } from "./browser.ts";

const lab = resolve(import.meta.dirname, "../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

/** Every selector of a stylesheet (comments removed, @keyframes bodies skipped). */
export function selectors(css: string): string[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  let depth = 0;
  let skipFrom = -1;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "{") {
      const prelude = text.slice(start, i).trim();
      if (skipFrom < 0) {
        if (prelude.startsWith("@keyframes")) skipFrom = depth;
        else if (!prelude.startsWith("@")) out.push(...prelude.split(",").map((s) => s.trim()));
      }
      depth += 1;
      start = i + 1;
    } else if (text[i] === "}") {
      depth -= 1;
      if (depth === skipFrom) skipFrom = -1;
      start = i + 1;
    } else if (text[i] === ";" ) {
      start = i + 1;
    }
  }
  return out;
}

/** The custom properties declared in the first block whose selector is exactly `selector`. */
function tokens(css: string, selector: string): Map<string, string> {
  const block = new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`).exec(css);
  assert.ok(block, `no ${selector} block`);
  return new Map([...block[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

const SHARED_TOKENS = ["--background", "--foreground", "--card", "--card-foreground", "--muted", "--muted-foreground", "--border", "--input", "--ring", "--primary", "--primary-foreground", "--destructive", "--success", "--warning", "--radius"];

test("UX00-S01 every lab.css selector is scoped to the .lab root or a lab- class: no global element rule leaks", () => {
  const all = selectors(read("app/lab.css"));
  assert.ok(all.length > 10, "the stylesheet was read");
  const leaks = all.filter((s) => !/^\.lab(?![\w-])|^\.lab-[a-z]/.test(s));
  assert.deepEqual(leaks, []);
  assert.match(read("app/layout.tsx"), /import "\.\/lab\.css";/);
  assert.match(read("app/layout.tsx"), /<html lang="en" className=\{`lab \$\{sans\.variable\} \$\{mono\.variable\}`\}>/, "the scope is the document root, so portals inherit it");
});

test("UX00-S02 the Lab's tokens are the App's dark theme values, one per shared name", () => {
  const app = readFileSync(join(appDir, "app/globals.css"), "utf8");
  const appDark = new Map([...tokens(app, ":root"), ...tokens(app, ".dark")]);
  const labTokens = tokens(read("app/lab.css"), ".lab");
  for (const name of SHARED_TOKENS) {
    assert.ok(appDark.has(name), `the App defines ${name}`);
    assert.equal(labTokens.get(name), appDark.get(name), name);
  }
});

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

test("UX00-S03 no production module reaches the synthetic harness or its fixtures", () => {
  const sources = ["app", "lib", "components"].flatMap((d) => walk(join(lab, d))).concat(join(lab, "proxy.ts"));
  const offenders = sources
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.ts$/.test(p))
    .filter((p) => /from\s+["'][^"']*tests\//.test(readFileSync(p, "utf8")))
    .map((p) => relative(lab, p));
  assert.deepEqual(offenders, []);
});

let h: Harness;
before(async () => {
  h = await harness();
});
after(async () => {
  await h?.stop();
});

const FRAMES = "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))";

async function open(viewport: { width: number; height: number }, init?: string): Promise<Page> {
  const page = await h.browser.newPage({ viewport, reducedMotion: "reduce" });
  if (init) await page.addInitScript(init);
  await page.goto(`${h.url}/`);
  await page.getByRole("heading", { name: "Primitives" }).waitFor();
  // Hydrated (React has claimed the form's input), then a frame for the islands' effects to run.
  await page.waitForFunction(`Object.keys(document.querySelector("form input")).some((k) => k.startsWith("__reactFiber"))`, undefined, { timeout: 30_000 });
  await page.evaluate(FRAMES);
  return page;
}

/** A key press at human cadence: Base UI's focus guards hand focus on in the next animation frame. */
async function press(page: Page, key: string) {
  await page.keyboard.press(key);
  await page.evaluate(FRAMES);
}

const inside = (selector: string) => `!!document.activeElement && !!document.activeElement.closest(${JSON.stringify(selector)})`;

async function keyboardModal(page: Page, trigger: string, title: string) {
  await page.getByRole("button", { name: trigger, exact: true }).focus();
  await press(page, "Enter");
  const dialog = page.getByRole("dialog", { name: title });
  await dialog.waitFor({ state: "visible" });
  assert.equal(await page.evaluate<boolean>(inside("[role=dialog]")), true, `${title}: focus moves into the modal`);
  for (let i = 0; i < 8; i += 1) {
    await press(page, "Tab");
    assert.equal(await page.evaluate<boolean>(inside("[role=dialog]")), true, `${title}: Tab ${i + 1} stays inside the modal`);
  }
  for (let i = 0; i < 3; i += 1) {
    await press(page, "Shift+Tab");
    assert.equal(await page.evaluate<boolean>(inside("[role=dialog]")), true, `${title}: Shift+Tab stays inside the modal`);
  }
  await press(page, "Escape");
  await dialog.waitFor({ state: "detached" });
  assert.equal(await page.evaluate<string>("document.activeElement?.textContent ?? ''"), trigger, `${title}: Escape returns focus to the trigger`);
}

test("UX00-K01 the dialog takes focus, keeps Tab inside, closes on Escape and returns focus, at desktop and mobile widths", async () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    const page = await open(viewport);
    await keyboardModal(page, "Open dialog", "Synthetic dialog");
    await page.close();
  }
});

test("UX00-K02 the closed drawer has nothing focusable; the open drawer is a modal at the start edge", async () => {
  const page = await open(MOBILE);
  assert.equal(await page.getByText("First link").count(), 0, "closed: its links are not in the document, so not in the tab order");
  await page.getByRole("button", { name: "Open drawer", exact: true }).click();
  await page.getByRole("dialog", { name: "Synthetic drawer" }).waitFor({ state: "visible" });
  const left = await page.evaluate<number>(`document.querySelector("[role=dialog]").getBoundingClientRect().left`);
  assert.equal(left, 0, "the drawer starts at the start edge, inside the viewport");
  await press(page, "Escape");
  await page.getByRole("dialog", { name: "Synthetic drawer" }).waitFor({ state: "detached" });
  await keyboardModal(page, "Open drawer", "Synthetic drawer");
  await page.close();
});

test("UX00-K03 a field's label, description and error are tied to its control, which is marked invalid", async () => {
  const page = await open(DESKTOP);
  const input = page.getByRole("textbox", { name: "Model reference" });
  assert.equal(await input.getAttribute("aria-invalid"), "true");
  const ids = ((await input.getAttribute("aria-describedby")) ?? "").split(/\s+/).filter(Boolean);
  const described = await page.evaluate<string>(`${JSON.stringify(ids)}.map((id) => document.getElementById(id)?.textContent ?? "").join(" | ")`);
  assert.match(described, /The immutable revision id\./);
  assert.match(described, /Enter a revision id\./);
  await page.close();
});

const CLIPBOARD = `Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: (text) => new Promise((resolve, reject) => { window.__copy = { text, resolve, reject }; }) } });`;

test("UX00-K04 the copy button says Copied only after the clipboard resolves, and a failure leaves selectable text", async () => {
  const page = await open(DESKTOP, CLIPBOARD);
  const button = page.getByRole("button", { name: "Copy serving version id" });
  await button.click();
  assert.match(await page.evaluate<string>("window.__copy.text"), /^sv-00000000-synthetic/);
  assert.doesNotMatch((await page.locator(".lab-copy [role=status]").textContent()) ?? "", /Copied/, "no success before the write resolves");
  await page.evaluate("window.__copy.resolve()");
  await page.evaluate(FRAMES);
  assert.equal(await page.locator(".lab-copy [role=status]").textContent(), "Copied");
  await page.close();

  const failing = await open(DESKTOP, CLIPBOARD);
  await failing.getByRole("button", { name: "Copy serving version id" }).click();
  await failing.evaluate(`window.__copy.reject(new Error("denied"))`);
  await failing.evaluate(FRAMES);
  assert.match((await failing.locator(".lab-copy [role=status]").textContent()) ?? "", /^Copy failed/);
  assert.equal(await failing.locator(".lab-copy code").count(), 1, "the value stays readable to select by hand");
  assert.match((await failing.locator(".lab-copy code").textContent()) ?? "", /^sv-00000000-synthetic/);
  await failing.close();
});

test("UX00-K05 every service state keeps the page heading, names itself and announces with the right role", async () => {
  const page = await open(DESKTOP);
  for (const state of ["loading", "empty", "unavailable", "denied", "not_found", "stale"]) {
    const role = await page.locator(`[data-state="${state}"]`).getAttribute("role");
    assert.equal(role, state === "unavailable" ? "alert" : "status", state);
    assert.equal(await page.getByRole("heading", { name: `Synthetic ${state} state` }).count(), 1, state);
  }
  assert.equal(await page.getByRole("heading", { name: "Primitives", exact: true }).count(), 1, "the page header stays");
  const pending = page.getByRole("button", { name: "Saving" });
  assert.equal(await pending.isDisabled(), true, "a pending submit cannot be sent twice");
  assert.equal(await pending.getAttribute("aria-busy"), "true");
  await page.close();
});

test("UX00-K06 at 320px long identifiers wrap: the page never scrolls sideways", async () => {
  const page = await open({ width: 320, height: 720 });
  const [scroll, width] = await page.evaluate<[number, number]>("[document.documentElement.scrollWidth, window.innerWidth]");
  assert.ok(scroll <= width, `scrollWidth ${scroll} > ${width}`);
  await page.close();
});
