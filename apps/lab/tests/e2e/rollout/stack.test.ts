// LAB-E2E rollout = E8L k10's UI half: the Lab's releases page, served by the built Lab app and signed in
// through its own form, over lab-api's /lab/v1/releases as the gateway composes it (LAB_RELEASES: D9's
// real store, 0043's real proposal store, 0053's decisions; releases launched and decided by `rollout
// launch|decide`'s own code - backend.py, key l4): the verdict shown, a proposal at the fence the page
// showed through the page's own server action, the operator's approval, a guardrail and an emergency
// rollback, and the unsafe variants. Skipped unless LAB_E2E_REAL=1 (Docker, l4):
//   cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/rollout/stack.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { ACCESS_COPY } from "../../../lib/auth/access.ts";
import { REFUSAL_COPY } from "../../../lib/services/rollouts/view.ts";
import { door, form, forms, record, section, SKIP, stack, text, type Browser } from "../harness.ts";

type World = { A: string; B: string; operator: string; controller: string; users: Record<string, string>; composed: Record<string, boolean>; stand_ins: string[] };

test("E2E-R k10 the releases UI: verdict, proposal, approval and emergency rollback", { skip: SKIP }, async (t) => {
  const s = await stack<World>("rollout");
  t.after(s.stop);
  const w = s.world;
  const as = async (who: string): Promise<Browser> => {
    const b = s.browser();
    assert.equal((await b.signIn(`${who}@lab.e2e`)).location, "/", `${who} signs in`);
    return b;
  };
  const admin = await as("admin");
  const launch = async (tag: number) => (await door(s.api, "launch", { tag })).policy_ref as string;
  const step = async (ref: string, body: object) => (await door(s.api, "step", { policy_ref: ref, ...body })).action;
  const decide = async (ref: string, approve: boolean) => (await door(s.api, "decide", { policy_ref: ref, approve })).result;
  const releases = async (b: Browser = admin) => (await b.get("/releases")).html;
  const shown = async (ref: string, b: Browser = admin) => {
    const html = section(await releases(b), ref);
    assert.ok(html !== null, `the page shows ${ref}`);
    return { text: text(html), forms: forms(html), html };
  };
  const propose = async (ref: string, button: RegExp, b: Browser = admin, from: Browser = admin) =>
    (await b.submit("/releases", form((await shown(ref, from)).html, button))).location;

  await t.test("E2E-R01 as the gateway composes LAB_RELEASES today, the page fails closed: no rows, no form, no success", async () => {
    await door(s.api, "composition", { as: "gateway" });
    // WR-R4-2 composed records, proposals and store (merge #50): the records refuse a release D9
    // started without the plan its launcher stores first (R241, a 503 naming WR-C5-PLAN), and
    // R3's variant listing is not composed (WR-C6-VARIANTS)
    const planless = (await door(s.api, "launch", { tag: 9, planless: true })).policy_ref as string;
    // the refusal is WR-C5-PLAN's own, not any 503: the gateway's records port itself names it
    assert.match(String((await door(s.api, "probe")).refusal), /is not stored \(WR-C5-PLAN\)/);
    for (const path of ["/releases", "/optimizations"]) {
      const page = await admin.get(path);
      assert.ok(page.text.includes(REFUSAL_COPY.unavailable) && forms(page.html).every((f) => !/Propose/.test(f.text)), path);
    }
    const consumer = await as("consumer");
    assert.ok((await consumer.get("/releases")).text.includes(ACCESS_COPY.denied));
    // the operator stops it: every later release is launched by `rollout launch`, its plan stored
    await door(s.api, "stop", { policy_ref: planless });
    await door(s.api, "composition", { as: "journey" });
  });

  await t.test("E2E-R02 a guardrail breach is one D9 rollback the page shows from the records, with nothing left to propose", async () => {
    const ref = await launch(1);
    assert.equal(await step(ref, { errors: 21 }), "rollback");
    const row = await shown(ref);
    assert.match(row.text, /Status rolled back · serving returns to lab:serving:/);
    assert.match(row.text, new RegExp(`rollback by ${w.controller} at \\S+: error_rate`));
    assert.deepEqual(row.forms, [], "no proposal on a rolled-back release");
  });

  await t.test("E2E-R03 an expand verdict → the administrator proposes at the shown fence → the operator approves → approved, with its evidence", async () => {
    const ref = await launch(2);
    assert.equal(await step(ref, { report: "accept" }), "expand");
    const before = await shown(ref);
    assert.deepEqual(before.forms.map((f) => f.text), ["Propose expansion", "Propose rollback"]);
    assert.deepEqual(before.forms[0].fields.filter(([k]) => !k.startsWith("$")), [["policyRef", ref], ["fence", "1"], ["kind", "expand"]]);
    assert.equal(await propose(ref, /Propose expansion/), "/releases", "the action returns to the records, claiming nothing");
    const pending = await shown(ref);
    assert.match(pending.text, /Request expand proposed · awaiting operator approval/);
    assert.match(pending.text, /Status running · expand/, "a proposal changes nothing");
    assert.deepEqual(pending.forms, [], "one request at a time");
    assert.equal(await decide(ref, true), "ok");
    const approved = await shown(ref);
    assert.match(approved.text, /Status expansion approved by an operator/);
    assert.match(approved.text, new RegExp(`expand by ${w.operator} at \\S+ · evidence lab:run:\\S+, lab:run:`));
    assert.match(approved.text, /Request —/);
  });

  await t.test("E2E-R04 an emergency rollback: the administrator proposes it, the operator approves it, the release rolls back once", async () => {
    const ref = await launch(3);
    assert.equal(await step(ref, { report: "inconclusive" }), "hold");
    const held = await shown(ref);
    assert.match(held.text, /Promotion Promotion blocked: the evaluation is inconclusive\./);
    assert.deepEqual(held.forms.map((f) => f.text), ["Propose rollback"], "an inconclusive verdict is never proposed for expansion");
    assert.equal(await propose(ref, /Propose rollback/), "/releases");
    assert.equal(await decide(ref, true), "ok");
    const row = await shown(ref);
    assert.match(row.text, /Status rolled back · serving returns to /);
    assert.equal([...row.text.matchAll(/rollback by /g)].length, 1);
  });

  await t.test("E2E-R05 unsafe proposals are refused with fixed copy: a viewer, another provider, a stale fence, a double click; a rejection changes nothing", async () => {
    const ref = await launch(4);
    assert.equal(await step(ref, { report: "accept" }), "expand");
    const viewer = await as("viewer");
    assert.deepEqual((await shown(ref, viewer)).forms, [], "a viewer reads, and has no form");
    assert.equal(await propose(ref, /Propose rollback/, viewer), "/releases?refused=denied");
    assert.ok((await viewer.get("/releases?refused=denied")).text.includes(REFUSAL_COPY.denied));
    const other = await as("other_dev");
    assert.equal(section(await releases(other), ref), null, "another provider does not see it");
    assert.equal(await propose(ref, /Propose rollback/, other), "/releases?refused=denied", "and cannot propose on it");
    const stale = form((await shown(ref)).html, /Propose expansion/);
    const again = form((await shown(ref)).html, /Propose rollback/);
    assert.equal((await admin.submit("/releases", again)).location, "/releases");
    assert.equal((await admin.submit("/releases", again)).location, "/releases?refused=conflict", "a double click is one proposal");
    assert.equal(await decide(ref, false), "rejected");
    const rejected = await shown(ref);
    assert.match(rejected.text, /Status running · expand/);
    assert.match(rejected.text, /Request —/);
    assert.match(rejected.text, /No decisions yet\./, "a rejection is no D9 decision");
    assert.equal(await step(ref, { errors: 21 }), "rollback");
    assert.equal((await admin.submit("/releases", stale)).location, "/releases?refused=conflict", "the fence the page showed has moved");
    assert.ok((await admin.get("/releases?refused=conflict")).text.includes(REFUSAL_COPY.conflict));
  });
  record("rollout", { cell: "E8L k10", composed: w.composed, stand_ins: w.stand_ins });
});
