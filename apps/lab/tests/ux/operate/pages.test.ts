// UX-03 operate pages (UX-T07 roles, UX-T08 registration, UX-T09 partial services), the real pages in
// the real provider layout (render.ts) over the labelled preview fake (LAB_CONTROL_PREVIEW=1) seeded
// with synthetic records, laid out in Chromium at 390/768/1440 and walked by keyboard.
import assert from "node:assert/strict";
import test from "node:test";
import { launch, load, overflows, page, route, tabWalk, text, VIEWPORTS, type Membership, type Role, type Shot } from "./render.ts";

Object.assign(process.env, { LAB_CONTROL_PREVIEW: "1" });
type Port = typeof import("../../../lib/services/control/port.ts");
type Fake = import("../../../lib/services/control/fake.ts").FakeControl;
const { controlPort } = await load<Port>("lib/services/control/port.ts");
const fake = controlPort() as Fake;
const real = { aggregates: fake.aggregates.bind(fake), proposals: fake.proposals.bind(fake), deployments: fake.deployments.bind(fake), models: fake.models.bind(fake) };
const restore = () => Object.assign(fake, real);
const DOWN = async () => ({ ok: false as const, reason: "unavailable" as const });

const P = "11111111-1111-4111-8111-111111111111";
const EMPTY = "22222222-2222-4222-8222-222222222222";
const DIGEST = `sha256:${"a".repeat(64)}`;
fake.importModel(P, "synthetic/alpha-2b", [DIGEST]);
fake.observe(P, { deploymentRevisionId: "d-synthetic", windowStart: "2026-09-30T09:00:00Z", windowEnd: "2026-09-30T10:00:00Z", requests: 0, errors: 0, p95LatencyMs: null });

const as = (role: Role, providerId = P): Membership => ({ providerId, providerName: "Synthetic Lab", role });
const at = (file: string, path: string, role: Role, providerId = P, query: Record<string, string> = {}) => {
  const workspace = as(role, providerId);
  return route(file, { kind: "ready", workspace, workspaces: [workspace] }, path, query);
};

const browser = await launch();
test.after(() => browser.close());
async function open(body: string, viewport: { width: number; height: number } = VIEWPORTS[2]): Promise<Shot> {
  const p = await browser.newPage({ viewport });
  await p.setContent(page(body));
  return p;
}
/** At every width: no sideways scroll, and every control Tab reaches is drawn on screen. */
async function laidOut(markup: string, tabs = 12): Promise<void> {
  for (const viewport of VIEWPORTS) {
    const p = await open(markup, viewport);
    assert.equal(await overflows(p), false, `${viewport.width}px scrolls sideways`);
    const walk = await tabWalk(p, tabs);
    assert.ok(walk.every((t) => t.tag === "body" || t.onScreen), `${viewport.width}px focused an off-screen control: ${JSON.stringify(walk)}`);
    await p.close();
  }
}

test("OP-P01 overview: an empty workspace has one obvious next action; a viewer is told why it has none", async () => {
  const dev = await at("overview/page.tsx", "/overview", "developer", EMPTY);
  assert.equal(dev.split('href="/models/new"').length - 1, 1, "one Add model action");
  assert.match(dev, />Add model</);
  assert.match(dev, /<h1[^>]*>Overview<\/h1>/);
  const p = await open(dev);
  const shown = await text(p);
  for (const stage of ["Add a model", "Verify a private deployment", "Request publication"]) assert.ok(shown.includes(stage), stage);
  assert.match(shown, /Serving readiness is not available in this view/);
  await p.close();
  const viewer = await at("overview/page.tsx", "/overview", "viewer", EMPTY);
  assert.doesNotMatch(viewer, /href="\/models\/new"/);
  assert.match(viewer, /needs a developer or administrator/);
  await laidOut(dev);
});

test("OP-P02 overview: control records and measured traffic load and fail apart; neither failure becomes a zero", async () => {
  try {
    fake.aggregates = DOWN;
    const traffic = await at("overview/page.tsx", "/overview", "administrator");
    let p = await open(traffic);
    let shown = await text(p);
    assert.match(shown, /We couldn't load measured traffic/);
    assert.match(shown, /Registered deployment records 1/, `the control counts survive: ${shown}`);
    assert.doesNotMatch(shown, /No measured requests/);
    await p.close();
    restore();
    fake.deployments = DOWN;
    fake.proposals = DOWN;
    fake.models = DOWN;
    const control = await at("overview/page.tsx", "/overview", "administrator");
    p = await open(control);
    shown = await text(p);
    assert.match(shown, /We couldn't load control records/);
    assert.match(shown, /Couldn't check/, "the setup stages say unknown");
    assert.doesNotMatch(shown, /Registered deployment records 0|Public records 0/);
    assert.match(shown, /No measured requests/, "the traffic section still renders its own records");
    assert.match(shown, /Not available/);
    assert.match(shown, /Observed through/);
    assert.match(shown, /Last loaded/);
    await p.close();
  } finally {
    restore();
  }
});
