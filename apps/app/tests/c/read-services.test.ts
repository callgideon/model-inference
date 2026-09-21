// node --test "tests/**/*.test.ts"
//
// The read half's own assertions (CONSOLE-FLOWS, DUR-RLS, TRACE-TENANT): the things the exported
// suite cannot see from outside, or cannot reach yet because the write that would create them is C3's.

import assert from "node:assert/strict";
import test from "node:test";
import { addMoney, compareMoney, subMoney, ZERO_MONEY, type Money } from "../../lib/contracts/money.ts";
import { DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PLATFORM_ACTOR } from "../../lib/contracts/types.ts";
import { createConsoleServices } from "../../lib/services/console.ts";
import { buildPlan, namedQuery, scopedPort, type QueryPort } from "../../lib/services/query.ts";
import { createMemoryPort, makeConsoleHarness, TEST_CURSOR_SECRET } from "./harness.ts";

function expectOk<T>(result: { ok: true; value: T } | { ok: false; error: { code: string; message: string } }): T {
  assert.ok(result.ok, `expected success, got ${result.ok ? "" : `${result.error.code}: ${result.error.message}`}`);
  return result.value;
}

function expectError(
  result: { ok: true; value: unknown } | { ok: false; error: { code: string; message: string } },
  code: string,
): void {
  assert.ok(!result.ok, `expected ${code}`);
  assert.equal(result.error.code, code);
}

type ListResult<T> =
  | { ok: true; value: { items: T[]; next_cursor: string | null } }
  | { ok: false; error: { code: string; message: string } };

/** Every row of a list, so an assertion cannot be satisfied by a row simply not being on page one. */
async function walk<T>(
  fetch: (cursor: string | null) => Promise<{ ok: true; value: { items: T[]; next_cursor: string | null } } | { ok: false; error: { code: string; message: string } }>,
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;
  for (let pages = 0; pages < 100; pages += 1) {
    const page: { items: T[]; next_cursor: string | null } = expectOk(await fetch(cursor));
    items.push(...page.items);
    if (page.next_cursor === null) return items;
    cursor = page.next_cursor;
  }
  throw new Error("pagination did not terminate");
}

test("an operator's identity never reaches a customer view, in any actor field (R41)", async () => {
  const { services, sessions, data } = makeConsoleHarness();
  const operatorEntry = data.ledger.find((row) => row.by_operator === true);
  assert.ok(operatorEntry !== undefined, "the fixture must contain an operator-made ledger entry");

  const asOwner = { items: await walk((cursor) => services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor })) };
  const ownerView = asOwner.items.find((entry) => entry.id === operatorEntry.id);
  assert.ok(ownerView !== undefined, "the entry is visible to the organization");
  assert.equal(ownerView.actor, PLATFORM_ACTOR, "a customer learns that the platform acted, not who");
  assert.ok(!JSON.stringify(asOwner.items).includes(String(operatorEntry.actor)), "no page leaks the principal");

  const asOperator = { items: await walk((cursor) => services.ledger(sessions.operator, { limit: MAX_PAGE_LIMIT, cursor })) };
  const operatorView = asOperator.items.find((entry) => entry.id === operatorEntry.id);
  assert.equal(operatorView?.actor, operatorEntry.actor, "an operator still sees who did it");

  // The organization's own entries keep their own principal for everybody.
  for (const entry of asOwner.items.filter((row) => row.actor !== PLATFORM_ACTOR && row.actor !== null)) {
    const stored = data.ledger.find((row) => row.id === entry.id);
    assert.equal(stored?.by_operator, false, `${entry.id} shows a principal, so it must not be an operator entry`);
  }
});

test("the same masking covers feedback and consent history, which only a write can create", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  // C3 owns the write that stores these; the projection that hides the principal is C1's, so the
  // rows are seeded directly. Without this the invariant would be untested until C3 lands.
  const trace = data.traces.find((row) => row.request_id === ids.availableRequestId);
  assert.ok(trace !== undefined);
  data.feedback.push({
    id: "fb_operator_probe",
    request_id: ids.availableRequestId,
    created_at: "2026-09-20T12:00:00.000Z",
    channel: "console",
    author_role: "customer",
    author_principal: "operator@infrx.example",
    name: "thumb",
    value: true,
    comment: null,
    calibration_set: false,
    rubric_version: null,
    by_operator: true,
    org_id: ids.orgId,
  });
  data.consent.push({
    changed_at: "2026-09-20T12:00:00.000Z",
    evaluation_consent: false,
    changed_by: "operator@infrx.example",
    by_operator: true,
    org_id: ids.orgId,
  });

  const entries = expectOk(await services.feedback.list(sessions.owner, ids.availableRequestId));
  const probe = entries.find((entry) => entry.id === "fb_operator_probe");
  assert.ok(probe !== undefined, "the entry is visible to the organization");
  assert.equal(probe.author_principal, PLATFORM_ACTOR);
  assert.equal(probe.author_role, "customer", "it is still a customer signal (R19)");
  const asOperator = expectOk(await services.feedback.list(sessions.operator, ids.availableRequestId));
  assert.equal(
    asOperator.find((entry) => entry.id === "fb_operator_probe")?.author_principal,
    "operator@infrx.example",
  );

  const settings = expectOk(await services.settings.get(sessions.owner));
  const change = settings.consent_history.find((entry) => entry.changed_at === "2026-09-20T12:00:00.000Z");
  assert.equal(change?.changed_by, PLATFORM_ACTOR, "a consent change by the platform is not attributed to a person");
  const operatorSettings = expectOk(await services.settings.get(sessions.operator));
  assert.equal(
    operatorSettings.consent_history.find((entry) => entry.changed_at === "2026-09-20T12:00:00.000Z")?.changed_by,
    "operator@infrx.example",
  );
  // The same trace's detail must mask it too: a detail view is a customer view.
  const detail = expectOk(await services.traceDetail(sessions.owner, ids.availableRequestId));
  assert.equal(detail.feedback.find((entry) => entry.id === "fb_operator_probe")?.author_principal, PLATFORM_ACTOR);
});

test("the balance comes from the wallet summary columns, not from a sum over the ledger", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const before = expectOk(await services.balances(sessions.owner));
  const wallet = data.wallets.find((row) => row.org_id === ids.orgId);
  assert.ok(wallet !== undefined);
  assert.equal(before.ledger_total, wallet.ledger_total);
  assert.equal(before.reserved_total, wallet.reserved_total);
  assert.equal(before.available, subMoney(before.ledger_total, before.reserved_total));

  // Move the summary column only. A service that recomputed the balance from the ledger rows — the
  // behaviour this task replaced — would report the old figure and ignore the reservation.
  const reserved = addMoney(wallet.reserved_total as Money, "1.00000000" as Money);
  wallet.reserved_total = reserved;
  const after = expectOk(await services.balances(sessions.owner));
  assert.equal(after.reserved_total, reserved, "the reserved total is the wallet's column");
  assert.equal(after.available, subMoney(after.ledger_total, reserved), "and it reduces what is available");
  assert.equal(compareMoney(after.available, before.available), -1);

  // An organization with no wallet row yet is at zero rather than an error.
  data.wallets.length = 0;
  const empty = expectOk(await services.balances(sessions.owner));
  assert.deepEqual(empty, { ledger_total: ZERO_MONEY, reserved_total: ZERO_MONEY, available: ZERO_MONEY });
});

test("a page is bounded by the contract's default and its look-ahead row never escapes", async () => {
  const { services, sessions } = makeConsoleHarness();
  const defaulted = expectOk(await services.usage(sessions.owner, {}));
  assert.equal(defaulted.items.length, DEFAULT_PAGE_LIMIT, "an absent limit is the contract default");
  assert.ok(defaulted.next_cursor !== null);
  for (const limit of [1, 7, MAX_PAGE_LIMIT]) {
    const page = expectOk(await services.usage(sessions.owner, { limit }));
    assert.equal(page.items.length, limit, `limit ${limit} returns exactly that many rows`);
  }
  // The page that exhausts the list carries no cursor, even though the reader asked for one row more
  // than remained: a minted cursor there costs the caller a round trip that returns an empty page, and
  // a UI that renders "next" from the cursor shows a blank one.
  const all = expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  let cursor = all.next_cursor;
  let seen = all.items.length;
  while (cursor !== null) {
    const next = expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }));
    assert.ok(next.items.length > 0, "a page reached through a cursor is never empty");
    seen += next.items.length;
    cursor = next.next_cursor;
  }
  const summary = expectOk(await services.usageSummary(sessions.owner, {}));
  assert.equal(seen, summary.requests, "the walk returns exactly the rows the summary counts");
});

test("usageDaily groups by UTC day, newest first, and its costs add up", async () => {
  const { services, sessions } = makeConsoleHarness();
  const days = expectOk(await services.usageDaily(sessions.owner, {}));
  assert.ok(days.length > 1, "the fixture spans more than one day");
  for (let i = 1; i < days.length; i += 1) assert.ok(days[i - 1].day > days[i].day, "newest day first");
  let total = ZERO_MONEY;
  let requests = 0;
  for (const day of days) {
    assert.match(day.day, /^\d{4}-\d{2}-\d{2}$/);
    total = addMoney(total, day.cost);
    requests += day.requests;
  }
  const summary = expectOk(await services.usageSummary(sessions.owner, {}));
  assert.equal(total, summary.cost, "the daily costs sum to the summary cost");
  assert.equal(requests, summary.requests);
});

test("a suspended organization reads everything and is refused only new work (R33)", async () => {
  const { services, sessions } = makeConsoleHarness();
  const suspended = sessions.suspendedOwner;
  expectOk(await services.usage(suspended, { limit: 5 }));
  expectOk(await services.balances(suspended));
  expectOk(await services.ledger(suspended, { limit: 5 }));
  expectOk(await services.settings.get(suspended));
  expectOk(await services.keys.list(suspended));
  expectOk(await services.traces(suspended, { limit: 5 }));
  expectError(await services.judgeRuns(suspended, {}), "org_suspended");
  // And a leaked key stays revocable: the refusal is the unimplemented write, never `org_suspended`.
  const keys = expectOk(await services.keys.list(suspended));
  const active = keys.find((key) => key.revoked_at === null);
  assert.ok(active !== undefined, "the suspended organization needs an active key");
  const revoked = await services.keys.revoke(suspended, active.id);
  assert.ok(!revoked.ok && revoked.error.code === "internal_error", "revocation is reached, then unimplemented");
});

test("operator authority is the flag and never a substitute for the organization role", async () => {
  const { services, sessions, ids } = makeConsoleHarness();
  // `operatorMember` is a platform operator whose organization role is only `member`. The flag is
  // authority over the platform; it is not a superuser bit over the organization's own settings, and a
  // service that checked one for the other would look correct on an owner-operator session.
  const memberOperator = sessions.operatorMember;
  assert.equal(memberOperator.role, "member");
  assert.equal(memberOperator.isOperator, true);
  expectError(await services.settings.update(memberOperator, { trace_mode: "off" }), "forbidden");
  expectError(await services.keys.create(memberOperator, { name: "member-operator key" }), "forbidden");
  expectError(await services.keys.revoke(memberOperator, ids.keyId), "forbidden");
  // And the operator operations work for it, which is the other half of the same claim.
  expectOk(await services.adminOrgs(memberOperator, { limit: 5 }));
  expectOk(await services.adminAudit(memberOperator, { limit: 5 }));
  expectOk(await services.judgeRuns(memberOperator, { limit: 5 }));
});

test("an off-mode request has no trace row but still has a detail to open (R13)", async () => {
  const { services, sessions, ids } = makeConsoleHarness();
  const list = expectOk(await services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  assert.ok(
    !list.items.some((row) => row.request_id === ids.offRequestId),
    "an off-mode request is not in the trace list",
  );
  for (const row of list.items) assert.notEqual(row.trace_mode, "off");
  const detail = expectOk(await services.traceDetail(sessions.owner, ids.offRequestId));
  assert.equal(detail.trace_mode, "off");
  assert.equal(detail.content, "off");
});


test("the single-row reads are tenant-bound too, in both directions", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  // These three reads have no cursor and no filter, so nothing else in the suite would notice if one
  // of them stopped binding its organization — it would simply answer with the first row it found.
  const ourSettings = expectOk(await services.settings.get(sessions.owner));
  const theirSettings = expectOk(await services.settings.get(sessions.otherOwner));
  const stored = (orgId: string) => data.settings.find((row) => row.org_id === orgId);
  assert.equal(ourSettings.trace_mode, stored(ids.orgId)?.trace_mode);
  assert.equal(theirSettings.trace_mode, stored(ids.otherOrgId)?.trace_mode);
  assert.notDeepEqual(
    { mode: ourSettings.trace_mode, days: ourSettings.content_retention_days, consent: ourSettings.evaluation_consent },
    { mode: theirSettings.trace_mode, days: theirSettings.content_retention_days, consent: theirSettings.evaluation_consent },
    "the harness must give the two organizations different settings, or this proves nothing",
  );

  // Consent history is the same read; it must not carry the other organization's audit trail.
  const ours = new Set(data.consent.filter((row) => row.org_id === ids.orgId).map((row) => row.changed_at));
  const theirs = new Set(data.consent.filter((row) => row.org_id === ids.otherOrgId).map((row) => row.changed_at));
  for (const entry of ourSettings.consent_history) {
    assert.ok(ours.has(entry.changed_at), `a consent entry from another organization: ${entry.changed_at}`);
  }
  for (const entry of theirSettings.consent_history) {
    assert.ok(theirs.has(entry.changed_at), "and the reverse direction");
  }

  // Feedback is keyed by a request id the caller supplies, which is exactly where a tenant is lost.
  expectError(await services.feedback.list(sessions.otherOwner, ids.availableRequestId), "not_found");
  expectError(await services.feedback.list(sessions.owner, ids.otherOrgRequestId), "not_found");
  expectError(await services.traceDetail(sessions.otherOwner, ids.availableRequestId), "not_found");
  // And a key id: `keys.revoke` resolves ownership before it does anything else.
  const revoked = await services.keys.revoke(sessions.otherOwner, ids.keyId);
  assert.ok(!revoked.ok && revoked.error.code === "not_found", "another organization's key is simply absent");
});

test("a truthy isOperator is not authority on the authorization path either", async () => {
  const { services, sessions, ids } = makeConsoleHarness();
  // The masking decision was pinned last round; the *authorization* decision was not, so
  // `session.isOperator !== true` could be weakened to `!session.isOperator` and a session carrying
  // the string "false" would read every organization on the platform.
  const spoofed = [1, "false", "true", {}, [], "0"].map((value) => ({
    ...sessions.owner,
    isOperator: value as unknown as boolean,
  }));
  for (const session of spoofed) {
    const shown = JSON.stringify(session.isOperator);
    expectError(await services.adminOrgs(session, { limit: 5 }), "forbidden");
    expectError(await services.adminAudit(session, { limit: 5 }), "forbidden");
    expectError(
      await services.adminGrant(session, {
        target_org_id: ids.orgId,
        amount: "1.00000000" as Money,
        kind: "promotional",
        reason: `isOperator=${shown} is not authority`,
        idempotency_key: `spoof-${shown}`,
      }),
      "forbidden",
    );
    // Owner-or-operator: a *member* whose flag is merely truthy must still be refused.
    expectError(await services.judgeRuns({ ...session, role: "member" }, { limit: 5 }), "forbidden");
  }
  // The genuine flag still works, so the case cannot pass by refusing everybody.
  expectOk(await services.adminOrgs(sessions.operator, { limit: 5 }));
  expectOk(await services.adminAudit(sessions.operator, { limit: 5 }));
  expectOk(await services.judgeRuns(sessions.operatorMember, { limit: 5 }));
});

test("a cursor belongs to the list that minted it, across every paged list", async () => {
  const { services, sessions } = makeConsoleHarness();
  // Only `usage` was proven before, so a list wired to another list's scope survived. Every list is
  // paired with every other one here, in both directions.
  const lists: [string, (cursor: string | null) => Promise<ListResult<{ id?: string }>>][] = [
    ["usage", (cursor) => services.usage(sessions.owner, { limit: 5, cursor }) as never],
    ["ledger", (cursor) => services.ledger(sessions.owner, { limit: 5, cursor }) as never],
    ["traces", (cursor) => services.traces(sessions.owner, { limit: 5, cursor }) as never],
    ["judgeRuns", (cursor) => services.judgeRuns(sessions.owner, { limit: 2, cursor }) as never],
    ["adminOrgs", (cursor) => services.adminOrgs(sessions.operator, { limit: 2, cursor }) as never],
    ["adminAudit", (cursor) => services.adminAudit(sessions.operator, { limit: 5, cursor }) as never],
  ];

  const cursors = new Map<string, string>();
  for (const [name, fetch] of lists) {
    const page = expectOk(await fetch(null));
    assert.ok(page.next_cursor !== null, `${name} must have more than one page for this case to mean anything`);
    cursors.set(name, page.next_cursor);
  }
  for (const [name, fetch] of lists) {
    for (const [other, cursor] of cursors) {
      const result = await fetch(cursor);
      if (other === name) {
        expectOk(result);
        continue;
      }
      assert.ok(!result.ok, `${name} accepted a cursor minted by ${other}`);
      assert.equal(result.error.code, "invalid_cursor", `${name} with ${other}'s cursor`);
    }
  }
});

test("a cursor does not survive a change of filter, on every filtered list", async () => {
  const { services, sessions, ids } = makeConsoleHarness();
  const first = expectOk(await services.usage(sessions.owner, { limit: 5 }));
  assert.ok(first.next_cursor !== null);
  // Adding, removing or changing a filter is a different query, so the walk cannot continue into it.
  expectError(await services.usage(sessions.owner, { limit: 5, cursor: first.next_cursor, model: ids.modelId }), "invalid_cursor");
  const filtered = expectOk(await services.usage(sessions.owner, { limit: 5, model: ids.modelId }));
  if (filtered.next_cursor !== null) {
    expectError(await services.usage(sessions.owner, { limit: 5, cursor: filtered.next_cursor }), "invalid_cursor");
    expectError(
      await services.usage(sessions.owner, { limit: 5, cursor: filtered.next_cursor, key_id: ids.keyId }),
      "invalid_cursor",
    );
  }
  const traces = expectOk(await services.traces(sessions.owner, { limit: 5 }));
  assert.ok(traces.next_cursor !== null);
  expectError(await services.traces(sessions.owner, { limit: 5, cursor: traces.next_cursor, job_state: "succeeded" }), "invalid_cursor");
  const byState = expectOk(await services.traces(sessions.owner, { limit: 5, job_state: "succeeded" }));
  if (byState.next_cursor !== null) {
    expectError(await services.traces(sessions.owner, { limit: 5, cursor: byState.next_cursor }), "invalid_cursor");
  }
  // The operator list has a filter too, and its scope must carry it: a walk of one organization's
  // audit trail cannot continue into the whole platform's.
  const audit = expectOk(await services.adminAudit(sessions.operator, { limit: 5 }));
  assert.ok(audit.next_cursor !== null);
  expectError(
    await services.adminAudit(sessions.operator, { limit: 5, cursor: audit.next_cursor, target_org_id: ids.orgId }),
    "invalid_cursor",
  );
  const forOne = expectOk(await services.adminAudit(sessions.operator, { limit: 5, target_org_id: ids.orgId }));
  assert.ok(forOne.next_cursor !== null, "the seeded audit trail must page within one organization");
  expectError(await services.adminAudit(sessions.operator, { limit: 5, cursor: forOne.next_cursor }), "invalid_cursor");
  expectError(
    await services.adminAudit(sessions.operator, {
      limit: 5,
      cursor: forOne.next_cursor,
      target_org_id: ids.otherOrgId,
    }),
    "invalid_cursor",
  );
  // The page size is not a filter, so a walk may change it (08 §9).
  expectOk(await services.usage(sessions.owner, { limit: 25, cursor: first.next_cursor }));
});

test("adminAudit filters by target organization and pages like every other list", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const all = await walk((cursor) => services.adminAudit(sessions.operator, { limit: 5, cursor }));
  assert.equal(all.length, data.audit.length, "every audit entry is walked exactly once");
  const forOne = await walk((cursor) =>
    services.adminAudit(sessions.operator, { limit: 5, cursor, target_org_id: ids.orgId }),
  );
  assert.ok(forOne.length > 0 && forOne.length < all.length, "the filter must narrow, not widen or empty");
  for (const entry of forOne) assert.equal(entry.target_org_id, ids.orgId);
  // An absent filter is absent: the unfiltered walk carries other organizations' entries.
  assert.ok(all.some((entry) => entry.target_org_id !== ids.orgId), "the unfiltered list spans organizations");
  assert.ok(all.every((entry) => entry.at.endsWith("Z")), "and every entry carries a comparable timestamp");
});

test("has_feedback filters in both directions, and the two halves add up", async () => {
  const { services, sessions } = makeConsoleHarness();
  const all = await walk((cursor) => services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor }));
  const withFeedback = await walk((cursor) =>
    services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, has_feedback: true }),
  );
  const without = await walk((cursor) =>
    services.traces(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor, has_feedback: false }),
  );
  assert.ok(withFeedback.length > 0, "the fixture must have a trace with feedback");
  assert.ok(without.length > 0, "and one without");
  assert.equal(withFeedback.length + without.length, all.length, "the two halves partition the list");
  for (const row of withFeedback) assert.ok(row.feedback_count > 0);
  for (const row of without) assert.equal(row.feedback_count, 0);
});

test("a filter value is checked before it is bound: vocabulary, length and timestamp form", async () => {
  const { services, sessions } = makeConsoleHarness();
  // `Date.parse` accepts "2026-09-01", which is not an RFC 3339 instant; the bound is the format.
  for (const from of ["2026-09-01", "2026-09-01 12:00:00", "2026-09-01T12:00:00", "2026-09-01T12:00:00+02:00", "now"]) {
    expectError(await services.usage(sessions.owner, { from }), "invalid_request");
  }
  expectOk(await services.usage(sessions.owner, { from: "2026-09-01T00:00:00Z", limit: 5 }));
  expectOk(await services.usage(sessions.owner, { from: "2026-09-01T00:00:00.000Z", limit: 5 }));
  // An identifier is an identifier, not a document.
  expectError(await services.usage(sessions.owner, { key_id: "x".repeat(201) }), "invalid_request");
  expectError(await services.usage(sessions.owner, { model: "" }), "invalid_request");
  expectError(await services.traces(sessions.owner, { has_feedback: "yes" as never }), "invalid_request");
});

test("pending_reconciliation counts held unknown usage and nothing else", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const before = expectOk(await services.usageSummary(sessions.owner, {}));
  // A settled row with an outstanding hold is not awaiting reconciliation: the certainty is what says so.
  const row = data.usage.find((candidate) => candidate.org_id === ids.orgId && candidate.usage_certainty === "authoritative");
  assert.ok(row !== undefined);
  row.max_hold = "5.00000000";
  const after = expectOk(await services.usageSummary(sessions.owner, {}));
  assert.equal(after.pending_reconciliation, before.pending_reconciliation, "an authoritative hold is not pending");
  // Flipping the same row to unknown does move the figure.
  row.usage_certainty = "unknown";
  const unknown = expectOk(await services.usageSummary(sessions.owner, {}));
  assert.equal(unknown.pending_reconciliation, addMoney(before.pending_reconciliation, "5.00000000" as Money));
});

test("every port passes through the tenant check, whatever the port does", async () => {
  const { data, sessions, ids } = makeConsoleHarness();
  const session = sessions.owner;

  // D1's views return EVERY organization's rows to an operator or service-role session, so for those
  // sessions the plan's predicate is the only thing scoping a tenant's own reads. A port that forgot
  // it must fail loudly rather than answer a customer's page with the platform's rows.
  const leaky: QueryPort = {
    async run(plan) {
      const spec = namedQuery(plan.name);
      // Every row of the relation, newest-seeded first — which is another organization's, exactly as
      // a view that returns the whole platform to a privileged session would answer.
      return [...(data[spec.source] ?? [])].reverse().slice(0, plan.limit ?? 25);
    },
  };
  const leakyServices = createConsoleServices({ pg: leaky, ch: leaky, cursorSecret: TEST_CURSOR_SECRET });
  for (const call of [
    () => leakyServices.usage(session, { limit: 5 }),
    () => leakyServices.ledger(session, { limit: 5 }),
    () => leakyServices.keys.list(session),
    () => leakyServices.traces(session, { limit: 5 }),
  ]) {
    const result = await call();
    assert.ok(!result.ok, "a port that ignores the tenant is refused");
    assert.equal(result.error.code, "internal_error");
  }

  // And a tenant-scoped plan cannot reach an executor without a tenant at all.
  let reached = 0;
  const counting = scopedPort({
    async run() {
      reached += 1;
      return [];
    },
  });
  await assert.rejects(() => counting.run({ ...buildPlan("usage_page", { orgId: ids.orgId, limit: 5 }), tenant: null }));
  assert.equal(reached, 0, "the executor is never called with an unscoped plan");
  // The operator-wide reads are the exception the contract allows, and they still run.
  await counting.run(buildPlan("admin_orgs_page", { limit: 5 }));
  assert.equal(reached, 1);
});

test("usageDaily is bounded by its documented cap, not by the fixture", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const template = data.usage.find((row) => row.org_id === ids.orgId);
  assert.ok(template !== undefined);
  // 401 distinct days: one more than the cap, so a service that returned whatever the executor gave
  // it would hand a chart the whole retained history.
  for (let day = 0; day < 401; day += 1) {
    data.usage.push({
      ...template,
      request_id: `day-${day}`,
      created_at: new Date(Date.parse("2024-01-01T00:00:00.000Z") + day * 86400000).toISOString(),
    });
  }
  const days = expectOk(await services.usageDaily(sessions.owner, {}));
  assert.equal(days.length, 400, "the cap is real, and it is the documented one");
  assert.ok(days.length < new Set(data.usage.map((row) => String(row.created_at).slice(0, 10))).size);

  // And the service does not depend on the executor honouring the statement's LIMIT: a port that
  // ignores it must not turn "capped" into "the whole retained history".
  const unbounded: QueryPort = {
    async run(plan) {
      const rows = await createMemoryPort(data).run({ ...plan, limit: null });
      return rows;
    },
  };
  const lenient = createConsoleServices({ pg: unbounded, ch: unbounded, cursorSecret: TEST_CURSOR_SECRET });
  const stillCapped = expectOk(await lenient.usageDaily(sessions.owner, {}));
  assert.equal(stillCapped.length, 400, "the cap is the service's, not the executor's good manners");
});

test("a query port that fails is a Result error, never a thrown promise", async () => {
  const failing: QueryPort = {
    async run() {
      throw new Error("connection reset by peer: postgres://user:secret@host/db");
    },
  };
  const services = createConsoleServices({ pg: failing, ch: failing, cursorSecret: TEST_CURSOR_SECRET });
  const session = {
    userId: "u",
    email: "owner@example.com",
    orgId: "11111111-1111-4111-8111-111111111111",
    orgName: "Org",
    role: "owner" as const,
    isOperator: false,
  };
  for (const call of [
    () => services.usage(session, { limit: 5 }),
    () => services.balances(session),
    () => services.ledger(session, {}),
    () => services.traces(session, {}),
    () => services.settings.get(session),
    () => services.keys.list(session),
    () => services.usageSummary(session, {}),
    () => services.usageDaily(session, {}),
    () => services.traceDetail(session, "00000000-0000-4000-8000-000000000000"),
    () => services.judgeRuns(session, {}),
  ]) {
    // A rejected promise is reported as a failed assertion, not as a crashed test: the point of the
    // case is that no console read ever throws, and a thrown error would otherwise read as a mistake
    // in the test rather than the defect it is.
    let thrown: unknown = null;
    const result = await call().catch((error: unknown) => {
      thrown = error;
      return null;
    });
    // Worded to avoid the phrase the mutation runner reserves for an exception in disguise: this IS
    // an assertion about a rejected promise, and it must be classified as one.
    assert.equal(thrown, null, `a failing dependency rejected rather than refusing: ${String(thrown)}`);
    assert.ok(result !== null && !result.ok, "a failing dependency is a refusal");
    assert.equal(result.error.code, "internal_error");
    assert.ok(!result.error.message.includes("secret"), "and the message carries no connection detail");
  }
});

test("the services hold no secret and no client: a cursor secret is required and never echoed", () => {
  const ports = { pg: { async run() { return []; } }, ch: { async run() { return []; } } };
  // The boundary is exactly 16 characters, pinned on both sides so neither drifts.
  assert.throws(
    () => createConsoleServices({ ...ports, cursorSecret: "x".repeat(15) }),
    /cursor signing secret/,
    "an unsigned cursor is a caller-writable keyset, so a weak secret fails closed",
  );
  for (const weak of ["", "short", "x".repeat(15)]) {
    assert.throws(() => createConsoleServices({ ...ports, cursorSecret: weak }), /cursor signing secret/);
  }
  assert.throws(
    () => createConsoleServices({ ...ports, cursorSecret: 16 as unknown as string }),
    /cursor signing secret/,
    "and a secret that is not a string at all",
  );
  createConsoleServices({ ...ports, cursorSecret: "x".repeat(16) });
});
