// node --test "tests/**/*.test.ts"
//
// V1 — tenant scope of the trace list (oracle TRACE-TENANT).
//
// The list has no organization parameter and cannot acquire one: the tenant is `session.orgId`. These
// cases are the ones that make that claim mean something — they use *more than one session*, which
// the first round of V1 evidence claimed without doing. What is provable here is that the page's own
// query cannot widen the tenant and that a cursor minted in one organization returns nothing in
// another; the enforcement itself is the service's, and C1 re-proves it against real PostgreSQL.
import assert from "node:assert/strict";
import test from "node:test";

import { parseTraceParams, traceHref } from "../../app/(console)/traces/query.ts";
import { buildTraceListView } from "../../app/(console)/traces/view-model.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import type { ApiKeySummary, SessionContext, TraceListItem } from "../../lib/contracts/types.ts";

const NOW = Date.parse("2026-09-21T12:00:00.000Z");
const WIDE = { range: "30d" as const };

type Loaded = { rows: TraceListItem[]; keys: ApiKeySummary[] };

async function load(
  services: ReturnType<typeof createFakeConsoleServices>,
  session: SessionContext,
  raw: Record<string, string> = {},
): Promise<Loaded> {
  const keysResult = await services.keys.list(session);
  assert.ok(keysResult.ok, `keys.list failed for ${session.email}`);
  const parsed = parseTraceParams({ ...WIDE, ...raw }, {
    now: NOW,
    keyIds: keysResult.value.map((key) => key.id),
  });
  const rows: TraceListItem[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 20; page += 1) {
    const result = await services.traces(session, {
      ...parsed.query,
      ...(cursor === null ? {} : { cursor }),
    });
    assert.ok(result.ok, `traces failed for ${session.email}: ${result.ok ? "" : result.error.code}`);
    rows.push(...result.value.items);
    cursor = result.value.next_cursor;
    if (cursor === null) return { rows, keys: keysResult.value };
    // A cursor is only accepted with the window pinned, which is what the page's links carry.
    parsed.filters.pinned = true;
  }
  throw new Error("the walk did not terminate");
}

test("V1-T01 each session sees its own organization's traces and nothing else", async () => {
  const services = createFakeConsoleServices();
  const { sessions } = services;

  const owner = await load(services, sessions.owner);
  const member = await load(services, sessions.member);
  const operator = await load(services, sessions.operator);
  const other = await load(services, sessions.otherOwner);
  const suspended = await load(services, sessions.suspendedOwner);

  assert.ok(owner.rows.length > 0, "the established organization has trace rows");
  assert.ok(suspended.rows.length > 0, "the suspended organization has trace rows of its own");

  // Role does not change the tenant: an owner, a member and a platform operator whose own
  // organization this is all read the same rows.
  const ids = (loaded: Loaded) => loaded.rows.map((row) => row.request_id);
  assert.deepEqual(ids(member), ids(owner), "a member reads the same list as the owner");
  assert.deepEqual(ids(operator), ids(owner), "an operator session reads its own organization");

  // Suspension gates new work, not reads (R33) — and the rows it reads are its own.
  assert.equal(
    ids(suspended).filter((id) => ids(owner).includes(id)).length,
    0,
    "the suspended organization shares rows with the first",
  );

  // The second organization's only key has tracing off, so its list is legitimately empty — and that
  // has to read as "nothing is captured", never as a failure or as someone else's traffic.
  assert.deepEqual(ids(other), [], "an organization with every key off has no trace rows (R13)");
  const idle = parseTraceParams({}, { now: NOW, keyIds: other.keys.map((key) => key.id) });
  const otherView = buildTraceListView({ ok: true, value: { items: [], next_cursor: null } }, {
    filters: idle.filters,
    keys: other.keys,
    narrowed: idle.narrowed,
  });
  assert.equal(otherView.kind, "empty");
  if (otherView.kind === "empty") assert.equal(otherView.reason, "tracing_off");

  // Disjoint: no key id crosses between organizations either.
  for (const [name, loaded] of [
    ["the second organization", other],
    ["the suspended organization", suspended],
  ] as const) {
    const keyOverlap = loaded.keys
      .map((key) => key.id)
      .filter((id) => owner.keys.some((own) => own.id === id));
    assert.deepEqual(keyOverlap, [], `${name} shares keys with the first`);
  }

  // Every row belongs to a key of the session that read it — the join the list view does, checked
  // against the row data rather than assumed.
  for (const [name, loaded] of [
    ["owner", owner],
    ["member", member],
    ["other owner", other],
    ["suspended owner", suspended],
  ] as const) {
    const own = new Set(loaded.keys.map((key) => key.id));
    for (const row of loaded.rows) {
      assert.ok(own.has(row.key_id), `${name} was shown a row keyed to another organization`);
    }
    // And the view never renders a name it does not have: an unknown key id would be shortened.
    const view = buildTraceListView({ ok: true, value: { items: loaded.rows.slice(0, 25), next_cursor: null } }, {
      filters: parseTraceParams(WIDE, { now: NOW, keyIds: [...own] }).filters,
      keys: loaded.keys,
    });
    if (view.kind === "rows") {
      for (const rendered of view.rows) {
        assert.ok(rendered.keyLabel.length > 0, `${name}: a row rendered without a key label`);
      }
    }
  }
});

test("V1-T02 a key or cursor from another organization returns nothing of that organization", async () => {
  const services = createFakeConsoleServices();
  const { sessions, ids } = services;
  const ownerKeys = await services.keys.list(sessions.owner);
  assert.ok(ownerKeys.ok);
  const otherKeys = await services.keys.list(sessions.otherOwner);
  assert.ok(otherKeys.ok);

  // The page refuses the foreign key id before it becomes a query at all (it is not in this
  // session's `keys.list`), so the reader is told rather than shown an empty list.
  const foreign = parseTraceParams(
    { ...WIDE, key: ids.otherOrgKeyId },
    { now: NOW, keyIds: ownerKeys.value.map((key) => key.id) },
  );
  assert.equal(foreign.query.key_id, undefined);
  assert.deepEqual(
    foreign.rejected.map((entry) => entry.name),
    ["key"],
  );

  // A cursor minted in one organization is bound to it. Used in another session it must not resume
  // that walk: the contract makes it `invalid_cursor`, and the page renders the way back.
  const firstPage = await services.traces(sessions.owner, { limit: 25 });
  assert.ok(firstPage.ok);
  const minted = firstPage.value.next_cursor;
  assert.ok(minted !== null, "the fixtures have more than one page");
  const ownerIds = firstPage.value.items.map((row) => row.request_id);

  const replayed = await services.traces(sessions.otherOwner, { limit: 25, cursor: minted });
  if (replayed.ok) {
    for (const row of replayed.value.items) {
      assert.ok(!ownerIds.includes(row.request_id), "a foreign cursor returned the other tenant's row");
    }
  } else {
    assert.equal(replayed.error.code, "invalid_cursor");
    const view = buildTraceListView(replayed, {
      filters: parseTraceParams({ ...WIDE }, { now: NOW, keyIds: [] }).filters,
      keys: otherKeys.value,
    });
    assert.equal(view.kind, "error");
    if (view.kind === "error") {
      assert.ok(!(view.action?.href ?? "").includes("cursor="), "and the way back drops it");
    }
  }

  // Nothing the page builds can name an organization: the only identifiers in a URL it produces are
  // this tenant's own key and an opaque cursor.
  const href = traceHref(
    parseTraceParams({ ...WIDE, key: ownerKeys.value[0].id }, {
      now: NOW,
      keyIds: ownerKeys.value.map((key) => key.id),
    }).filters,
  );
  assert.ok(!href.includes(ids.orgId) && !href.includes(ids.otherOrgId), href);
});
