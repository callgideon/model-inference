// LAB-E2E observe = E5L o10 (CONSOLE-FLOWS): the Lab's requests list and request review page, served by
// the built Lab app, signed in through its own form, over lab-api's provider trace read on the real L2
// and registry (backend.py, key l4). Skipped unless LAB_E2E_REAL=1 (Docker, l4):
//   cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/observe/stack.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { ACCESS_COPY } from "../../../lib/auth/access.ts";
import { CONTENT_COPY, FEEDBACK_COPY, TRACE_COPY } from "../../../components/traces/detail/view.ts";
import { JUDGE_COPY } from "../../../components/traces/judge/view.ts";
import { ACCESS_LABEL, LIST_COPY } from "../../../components/traces/list/view-model.ts";
import { door, record, SKIP, stack, type Browser } from "../harness.ts";

type Name = "granted" | "ungranted" | "lost" | "foreign" | "deleted" | "expired" | "ancient";
type World = { A: string; B: string; ids: Record<Name, string>; users: Record<string, string>; stand_ins: string[] };

test("E2E-O o10 the Lab review panel renders the provider trace route's answer", { skip: SKIP }, async (t) => {
  const s = await stack<World>("observe");
  t.after(s.stop);
  const { ids } = s.world;
  const as = async (who: string): Promise<Browser> => {
    const b = s.browser();
    const signedIn = await b.signIn(`${who}@lab.e2e`);
    assert.deepEqual([signedIn.status, signedIn.location], [303, "/"], `${who} signs in through the Lab's form`);
    return b;
  };
  const dev = await as("dev_a");
  // UX-05: a table row reads "<id> <started> UTC <elapsed> <model> <revision> <mode> <access label>[ Lost: <reason>]".
  const LABELS = Object.values(ACCESS_LABEL).map((l) => l.label).join("|");
  const row = (page: string, id: string) => new RegExp(`${id}[\\s\\S]*?\\s(${LABELS})(\\s+Lost: [a-z_]+)?`).exec(page)?.slice(1).filter(Boolean).join("").replace(/\s+/g, " ").trim() ?? null;
  const page = (id: string, query = "") => dev.get(`/requests/${id}${query}`);

  await t.test("E2E-O01 a developer's list shows its own deployment's requests with each one's content state, and nothing else", async () => {
    const list = await dev.get("/requests");
    assert.equal(list.status, 200);
    assert.deepEqual(
      Object.fromEntries((["granted", "ungranted", "lost", "expired"] as const).map((n) => [n, row(list.text, ids[n])])),
      { granted: ACCESS_LABEL.content.label, ungranted: ACCESS_LABEL.metadata.label, lost: `${ACCESS_LABEL.not_captured.label} Lost: spool_full`, expired: ACCESS_LABEL.expired.label });
    for (const gone of ["foreign", "deleted", "ancient"] as const) assert.ok(!list.text.includes(ids[gone]), `${gone} is listed`);
  });

  await t.test("E2E-O02 the review page shows the record, then each panel's own answer: granted content inline with its provenance, the feedback door, the judge door", async () => {
    const shared = await page(ids.granted);
    assert.match(shared.text, new RegExp(`Request ${ids.granted} .* Capture full .* Shared by organization [0-9a-f-]{36} Grant lab:grant:\\S+ Size 15 bytes`));
    assert.ok(!shared.text.includes(CONTENT_COPY.unread) && !shared.text.includes("Show content"), "granted content is read inline with the record (AP-07c)");
    assert.ok(shared.text.includes(FEEDBACK_COPY.not_found), "the feedback panel is 0038's door's answer (no feedback grant)");
    assert.ok(shared.text.includes(JUDGE_COPY.unavailable), "the judge panel is 0043's door's answer (submission off)");
    assert.ok((await page(ids.expired)).text.includes(CONTENT_COPY.expired));
    assert.ok((await page(ids.lost)).text.includes(CONTENT_COPY.not_captured));
    const deleted = await page(ids.deleted);
    assert.ok(deleted.text.includes(TRACE_COPY.not_found) && !deleted.text.includes("Capture full"), "a deleted request has no record");
  });

  await t.test("E2E-O03 a viewer, another provider, a consumer-only account and a signed-out visitor see no request", async () => {
    const viewer = await as("viewer_a");
    assert.ok((await viewer.get("/requests")).text.includes(TRACE_COPY.denied));
    const seen = await viewer.get(`/requests/${ids.granted}`);
    assert.ok(seen.text.includes(TRACE_COPY.denied) && seen.text.includes(FEEDBACK_COPY.forbidden) && !seen.text.includes("Capture full"));
    const other = await as("dev_b");
    assert.ok((await other.get("/requests")).text.includes(LIST_COPY.empty), "B lists nothing of A's");
    const foreign = await other.get(`/requests/${ids.granted}`);
    assert.ok(foreign.text.includes(TRACE_COPY.not_found) && !foreign.text.includes("Capture full"));
    const consumer = await as("consumer");
    const denied = await consumer.get("/requests");
    assert.ok(denied.text.includes(ACCESS_COPY.denied) && !denied.text.includes(ids.granted));
    const anon = await s.browser().get("/requests");
    assert.ok(anon.text.includes("Sign in") && !anon.text.includes(ids.granted));
  });

  await t.test("E2E-O04 the grantor revokes: the same request reads as revoked, with no organization or content", async () => {
    await door(s.api, "revoke");
    assert.equal(row((await dev.get("/requests")).text, ids.granted), ACCESS_LABEL.revoked.label);
    const after = await page(ids.granted);
    assert.ok(after.text.includes(CONTENT_COPY.revoked) && !after.text.includes("Shared by organization") && !after.text.includes("Size 15 bytes"));
  });
  record("observe", { cell: "E5L o10", stand_ins: s.world.stand_ins, composed: {} });
});
