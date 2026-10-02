// UX-03 acceptance journeys on fixtures (07-handoff UX-T07 roles, UX-T08 registration, UX-T09 partial
// services): the real provider layout, pages and server actions over the labelled preview fake and a
// fixture AP-04 port, at 390/768/1440 and by keyboard. Synthetic records only; nothing is captured.
// Fixture journeys do not certify the hosted services: that is api-lifecycle's and UX-11's.
import assert from "node:assert/strict";
import test from "node:test";
import { kit, load, mainOf, route, text, VIEWPORTS, type Membership, type Role, type Shot } from "./render.ts";

Object.assign(process.env, { LAB_CONTROL_PREVIEW: "1" });
type Fake = import("../../../lib/services/control/fake.ts").FakeControl;
const { controlPort } = await load<typeof import("../../../lib/services/control/port.ts")>("lib/services/control/port.ts");
const fake = controlPort() as Fake;
const control = await load<typeof import("../../../lib/services/control/actions.ts")>("lib/services/control/actions.ts");
const models = await load<typeof import("../../../app/(provider)/models/actions.ts")>("app/(provider)/models/actions.ts");
const wizard = await load<typeof import("../../../app/(provider)/models/new/actions.ts")>("app/(provider)/models/new/actions.ts");
const { open, laidOut, close } = await kit();
test.after(close);

const P = "55555555-5555-4555-8555-555555555555";
const DIGEST = `sha256:${"d".repeat(64)}`;
fake.importModel(P, "synthetic/delta-2b", [DIGEST]);
const G = globalThis as { __operateAccess?: unknown; __operateArtifacts?: unknown };
G.__operateArtifacts = { call: async () => ({ ok: true, status: 200, data: { data: [], next_cursor: null }, requestId: "r", location: null }) };
const as = (role: Role): Membership => {
  const workspace = { providerId: P, providerName: "Synthetic Lab", role };
  G.__operateAccess = { kind: "ready", workspace, workspaces: [workspace] };
  return workspace;
};
const at = (file: string, path: string, role: Role) => {
  const workspace = as(role);
  return route(file, { kind: "ready", workspace, workspaces: [workspace] }, path);
};
const form = (fields: Record<string, string>) => {
  const data = new FormData();
  for (const [k, v] of Object.entries(fields)) data.set(k, v);
  return data;
};
const landing = (run: Promise<unknown>) => run.then(() => "no redirect", (e: Error) => e.message.replace(/^NEXT_REDIRECT /, ""));
const PAGES = ["overview/page.tsx", "models/page.tsx", "models/new/page.tsx", "deployments/page.tsx", "settings/page.tsx"];
type Page = { default: (p: unknown) => Promise<unknown> };

/** Tab from the top until `name` is focused (at most `limit` presses); the names passed on the way. */
async function tabTo(p: Shot, name: RegExp, limit = 40): Promise<string[]> {
  await p.evaluate("document.activeElement?.blur(), window.scrollTo(0, 0)");
  const seen: string[] = [];
  for (let i = 0; i < limit; i += 1) {
    await p.keyboard.press("Tab");
    const now = await p.evaluate<string>("(document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent || '').trim()");
    seen.push(now);
    if (name.test(now)) return seen;
  }
  assert.fail(`Tab never reached ${name}: ${seen.join(" | ")}`);
}

test("OP-J01 UX-T07 roles: the same routes and actions answer by the session's role; no membership reaches no page", async () => {
  G.__operateAccess = { kind: "denied" };
  for (const file of PAGES) {
    const { default: Page } = await load<Page>(`app/(provider)/${file}`);
    await assert.rejects(Page({ searchParams: Promise.resolve({}), params: Promise.resolve({}) }), /NEXT_NOT_FOUND/, file);
  }
  const offers = async (role: Role) => ({
    add: /href="\/models\/new"/.test(mainOf(await at("overview/page.tsx", "/overview", role))),
    revise: /<legend>Identify<\/legend>/.test(mainOf(await at("models/page.tsx", "/models", role))),
    wizard: /<form/.test(mainOf(await at("models/new/page.tsx", "/models/new", role))),
    publish: />Request publication<\/summary>/.test(mainOf(await at("deployments/page.tsx", "/deployments", role))),
  });
  assert.deepEqual(await offers("viewer"), { add: false, revise: false, wizard: false, publish: false });
  assert.deepEqual(await offers("developer"), { add: true, revise: true, wizard: true, publish: false });
  as("administrator");
  const reg = await fake.register(as("developer"), { name: "delta-2b", artifactDigest: DIGEST, schemaVersion: "chat.v1", runtime: "vllm@sha256:dd" });
  assert.ok(reg.ok);
  await fake.smoke(as("developer"), reg.value.deploymentRevisionId);
  assert.deepEqual(await offers("administrator"), { add: true, revise: true, wizard: true, publish: true });
  // The server actions refuse the same roles the pages hide controls from, before any service is asked.
  as("viewer");
  assert.equal((await models.registerRevision(null, form({ name: "delta-2b", artifactDigest: DIGEST, schemaVersion: "s", runtime: "r" })))?.outcome?.kind, "refused");
  assert.match((await wizard.createProject(null, form({ name: "x", slug: "x", idempotencyKey: "0f0e0d0c-0b0a-4908-8706-050403020100" })))?.message ?? "", /does not allow/);
  as("developer");
  assert.equal(await landing(control.proposeChange(form({ kind: "publish", deploymentRevisionId: reg.value.deploymentRevisionId }))), "/deployments?refused=denied");
  for (const role of ["viewer", "developer", "administrator"] as const)
    assert.match(mainOf(await at("settings/page.tsx", "/settings", role)), /Read customer content<\/dt><dd>Not part of any role/);
});

test("OP-J02 UX-T08 registration: errors keep the input, the receipt is the record, a recorded smoke is not readiness, an administrator only requests", async () => {
  as("developer");
  const typed = { name: "delta-2b", artifactDigest: "sha256:not-a-digest", schemaVersion: "chat.v1", runtime: "vllm@sha256:dd" };
  const invalid = await models.registerRevision(null, form(typed));
  assert.deepEqual([invalid?.values, Object.keys(invalid?.errors ?? {})], [typed, ["artifactDigest"]]);
  const made = await models.registerRevision(null, form({ ...typed, artifactDigest: DIGEST }));
  assert.equal(made?.outcome?.kind, "registered");
  const record = made?.outcome?.kind === "registered" ? made.outcome.deployment : assert.fail("no record");
  assert.deepEqual([record.environment, record.visibility, record.state, record.smoke], ["dev", "private", "active", "none"]);
  let page = mainOf(await at("deployments/page.tsx", "/deployments", "developer"));
  assert.match(page, new RegExp(`${record.deploymentRevisionId}`));
  assert.match(page, /No smoke result is recorded/);
  await fake.smoke(as("developer"), record.deploymentRevisionId); // a recorded result from the old path
  page = mainOf(await at("deployments/page.tsx", "/deployments", "administrator"));
  assert.match(page, /Recorded smoke result: passed\. The record names no engine/);
  assert.doesNotMatch(page, /Ready|Healthy|Live\b/);
  as("administrator");
  assert.equal(await landing(control.proposeChange(form({ kind: "publish", deploymentRevisionId: record.deploymentRevisionId }))), "/deployments");
  const after = await at("deployments/page.tsx", "/deployments", "administrator");
  assert.match(mainOf(after), /Awaiting operator decision/);
  assert.doesNotMatch(mainOf(after), /Published|Approved by an operator/);
  // By keyboard at phone width: the request opens in place and its confirmation is reachable.
  const p = await open(after, VIEWPORTS[0]);
  await tabTo(p, /^Request publication$/);
  await p.keyboard.press("Enter");
  const next = await tabTo(p, /^Confirm publication request$/);
  assert.ok(next.length > 0);
  await p.close();
  await laidOut(after, 16);
});

test("OP-J03 UX-T09 partial services: each failed read keeps the page's heading and a reachable Try again, and is never a zero, an empty list or healthy", async () => {
  const DOWN = async () => ({ ok: false as const, reason: "unavailable" as const });
  const saved = { aggregates: fake.aggregates, proposals: fake.proposals, models: fake.models };
  try {
    fake.aggregates = DOWN;
    fake.proposals = DOWN;
    fake.models = DOWN;
    G.__operateArtifacts = { call: async () => ({ ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } }) };
    const cases: [string, string, RegExp, RegExp][] = [
      ["overview/page.tsx", "/overview", /We couldn't load measured traffic/, /Registered deployment records [1-9]/],
      ["deployments/page.tsx", "/deployments", /We couldn't load publication requests/, /Registered · active record/],
      ["models/page.tsx", "/models", /We couldn't load models/, /Models/],
      ["models/new/page.tsx", "/models/new", /We couldn't read model projects/, /Add model/],
    ];
    for (const [file, path, failure, kept] of cases) {
      const markup = await at(file, path, "administrator");
      const p = await open(markup);
      const shown = await text(p);
      assert.match(shown, failure, file);
      assert.match(shown, kept, `${file} keeps what did load`);
      assert.doesNotMatch(shown, /No measured requests|No publication requests yet|No models are registered|Healthy/, file);
      assert.ok((await p.evaluate<number>("document.querySelectorAll('h1').length")) === 1, `${file} keeps its heading`);
      assert.ok((await p.evaluate<number>("document.querySelectorAll('[role=alert]').length")) >= 1, `${file} announces the failure`);
      await tabTo(p, /^Try again$/);
      await p.close();
      await laidOut(markup, 8);
    }
  } finally {
    Object.assign(fake, saved);
  }
});
