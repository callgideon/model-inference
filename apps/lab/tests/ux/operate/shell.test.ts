// UX-03 L-01 (sign-in and workspace access) and the provider shell: the real provider layout rendered
// for synthetic access states (render.ts) and laid out in Chromium at 390/768/1440. Oracles: the
// grouped navigation marks only the current page; the workspace and role are named; the closed mobile
// menu keeps the sidebar out of the keyboard order; nothing scrolls sideways; the sign-in card says what
// access needs and offers no guessed recovery page; no-workspace, choose-workspace and unavailable stay
// distinct. Synthetic data only; nothing is captured.
import assert from "node:assert/strict";
import test from "node:test";
import { kit, overflows, route, tabWalk, text, VIEWPORTS, type Membership } from "./render.ts";

const LONG = "Synthetic Robotics Research Workspace With An Unusually Long Name For Wrapping Checks";
const W = (role: Membership["role"], name = "Synthetic Lab"): Membership => ({ providerId: `p-${role}`, providerName: name, role });
const ready = (path: string, workspaces = [W("developer")]) => route(null, { kind: "ready", workspace: workspaces[0], workspaces }, path);

const { open, close } = await kit();
test.after(close);

test("OP-S01 the shell groups Operate and Improve, marks only the current page and names the workspace and role", async () => {
  const markup = await ready("/deployments");
  const current = [...markup.matchAll(/<a[^>]*aria-current="page"[^>]*>([^<]*)</g)].map((m) => m[1]);
  assert.ok(current.length >= 1 && current.every((c) => c === "Deployments"), `current: ${current}`);
  const p = await open(markup, VIEWPORTS[2]);
  const shown = (await text(p)).toLowerCase(); // group headings are drawn uppercase
  for (const label of ["Operate", "Overview", "Models", "Deployments", "Requests", "Improve", "Settings", "Sign out", "Synthetic Lab", "Developer"])
    assert.ok(shown.includes(label.toLowerCase()), `${label} in: ${shown}`);
  for (const href of ["/overview", "/models", "/deployments", "/requests", "/datasets", "/evaluations", "/judge", "/annotations", "/training", "/releases", "/optimizations", "/settings"])
    assert.ok(markup.includes(`href="${href}"`), href);
  assert.match(markup, /<main[^>]*id="lab-main"/);
  assert.match(markup, /href="#lab-main"[^>]*>Skip to content</);
  await p.close();
});

test("OP-S02 the closed mobile menu keeps the sidebar out of the keyboard order; nothing scrolls sideways at any width", async () => {
  const markup = await ready("/overview", [W("administrator", LONG), W("viewer")]);
  for (const viewport of VIEWPORTS) {
    const p = await open(markup, viewport);
    assert.equal(await overflows(p), false, `${viewport.width}px scrolls sideways`);
    const tabs = await tabWalk(p, 6);
    assert.ok(tabs.every((t) => t.tag === "body" || t.onScreen), `${viewport.width}px focused an off-screen control: ${JSON.stringify(tabs)}`);
    const names = tabs.map((t) => t.name);
    if (viewport.width < 768) {
      assert.ok(names.includes("Open navigation"), `390: ${names}`);
      assert.ok(!names.includes("Overview") && !names.includes("Models"), `390 reaches the hidden sidebar: ${names}`);
    } else {
      assert.ok(!names.includes("Open navigation"), `${viewport.width}: the menu button is in the order: ${names}`);
      assert.ok(names.includes("Overview"), `${viewport.width}: ${names}`);
    }
    await p.close();
  }
});

test("OP-S03 the sign-in card explains provider access, labels its fields and offers no guessed recovery page", async () => {
  const markup = await route(null, { kind: "signed-out" }, "/");
  assert.match(markup, /<h1[^>]*>infrx Lab<\/h1>/);
  assert.match(markup, /Manage and improve your models/);
  assert.match(markup, /Access requires a provider workspace membership\./);
  assert.match(markup, /Looking for an inference endpoint\?/);
  assert.match(markup, /autoComplete="username"|autocomplete="username"/);
  assert.match(markup, /autocomplete="current-password"/i);
  assert.match(markup, /<label[^>]*>Email<\/label>/);
  assert.match(markup, /<label[^>]*>Password<\/label>/);
  assert.match(markup, /<button[^>]*type="submit"[^>]*>Sign in<\/button>/);
  assert.doesNotMatch(markup, /reset|forgot|recover/i);
  const p = await open(markup, VIEWPORTS[0]);
  assert.equal(await overflows(p), false);
  const tabs = (await tabWalk(p, 3)).map((t) => t.tag);
  assert.deepEqual(tabs, ["input", "input", "button"], "email, password, then Sign in");
  await p.close();
});

test("OP-S04 no workspace, choose-workspace and unavailable are distinct states", async () => {
  const none = await route(null, { kind: "denied" }, "/");
  assert.match(none, /This account has no Lab workspace/);
  assert.match(none, /Ask your provider administrator for access/);
  assert.match(none, />Sign out</);
  assert.doesNotMatch(none, /Open workspace|Try again/);
  const pick = await route(null, { kind: "select", workspaces: [W("viewer", "Alpha"), W("administrator", "Beta")] }, "/");
  assert.match(pick, /Choose a workspace/);
  assert.equal(pick.split(">Open workspace<").length - 1, 2);
  assert.match(pick, /Alpha[\s\S]*Viewer[\s\S]*Beta[\s\S]*Administrator/);
  assert.match(pick, /aria-label="Open workspace Alpha"/);
  const down = await route(null, { kind: "unavailable" }, "/");
  assert.match(down, /data-state="unavailable"/);
  assert.match(down, /Try again/);
  assert.doesNotMatch(down, /no Lab workspace|password/i, "an access-check failure is never a membership or credential verdict");
});
