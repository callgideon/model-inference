// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// The shared conformance suite applied to the fixture-backed fake, plus the cases that
// only a fake can exercise (failure injection, determinism). C runs the same
// `runConsoleServicesConformance` call against the real services in tests/c/.
import assert from "node:assert/strict";
import test from "node:test";
import { runConsoleServicesConformance } from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { CONSOLE_OPERATIONS } from "../../lib/contracts/services.ts";
import { compareMoney, ZERO_MONEY } from "../../lib/contracts/money.ts";
import { MAX_PAGE_LIMIT, TRACE_CONTENT_AVAILABILITY } from "../../lib/contracts/types.ts";

runConsoleServicesConformance(() => {
  const services = createFakeConsoleServices();
  return { services, sessions: services.sessions, ids: services.ids };
}, "fixture-backed ConsoleServices");

test("the fixtures are deterministic and each instance has its own state", async () => {
  const first = createFakeConsoleServices();
  const second = createFakeConsoleServices();
  const a = await first.usage(first.sessions.owner, { limit: MAX_PAGE_LIMIT });
  const b = await second.usage(second.sessions.owner, { limit: MAX_PAGE_LIMIT });
  assert.ok(a.ok && b.ok);
  assert.deepEqual(a.value, b.value, "two instances must produce identical rows");

  await first.settings.update(first.sessions.owner, { trace_mode: "minimal" });
  const untouched = await second.settings.get(second.sessions.owner);
  assert.ok(untouched.ok);
  assert.equal(untouched.value.trace_mode, "full", "one instance must not mutate another");
});

test("the fixture set covers what U and V need to build against", async () => {
  const services = createFakeConsoleServices();
  const usage = await services.usage(services.sessions.owner, { limit: MAX_PAGE_LIMIT });
  const traces = await services.traces(services.sessions.owner, { limit: MAX_PAGE_LIMIT });
  const ledger = await services.ledger(services.sessions.owner, { limit: MAX_PAGE_LIMIT });
  assert.ok(usage.ok && traces.ok && ledger.ok);
  // More than one full page of each, so pagination is exercised by real data.
  assert.equal(usage.value.items.length, MAX_PAGE_LIMIT);
  assert.ok(usage.value.next_cursor !== null, "usage must exceed one page");
  assert.ok(traces.value.next_cursor !== null, "traces must exceed one page");
  assert.ok(ledger.value.next_cursor !== null, "ledger must exceed one page");

  const seen = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await services.traces(services.sessions.owner, { limit: MAX_PAGE_LIMIT, cursor });
    assert.ok(page.ok);
    for (const row of page.value.items) seen.add(row.content);
    cursor = page.value.next_cursor;
  } while (cursor !== null);
  for (const state of TRACE_CONTENT_AVAILABILITY) {
    // R13: an off-mode request has no trace row, so `off` is reachable only through a usage row.
    if (state === "off") {
      assert.ok(!seen.has(state), "an off-mode request must not be listed as a trace");
      const detail = await services.traceDetail(services.sessions.owner, services.ids.offRequestId);
      assert.ok(detail.ok && detail.value.content === "off", "the off-mode request must still open");
      continue;
    }
    assert.ok(seen.has(state), `no fixture trace has content state ${state}`);
  }

  const balance = await services.balances(services.sessions.owner);
  assert.ok(balance.ok);
  assert.equal(compareMoney(balance.value.reserved_total, ZERO_MONEY), 1, "a hold must be outstanding");
  assert.equal(compareMoney(balance.value.available, balance.value.ledger_total), -1);

  const runs = await services.judgeRuns(services.sessions.owner, { limit: MAX_PAGE_LIMIT });
  assert.ok(runs.ok);
  assert.ok(
    runs.value.items.some((run) => run.state === "ambiguous"),
    "an ambiguous judge run must be available to V3",
  );
  assert.ok(
    runs.value.items.some((run) => run.mode === "dry_run"),
    "a dry-run judge run must be available to V3",
  );
  assert.ok(
    runs.value.items.some((run) => run.limited_evaluation_count > 0),
    "a limited evaluation must be available to V3",
  );
});

test("the exact overflow the review found is refused, and the fake stays usable afterwards", async () => {
  // Round 2 regression, reproduced precisely: the largest money is type-valid and parses, so only
  // the prospective wallet total can refuse it. Before the fix, the entry was appended first and
  // the refusal arrived as a thrown RangeError — a double grant on retry and permanently broken
  // balance reads. Fake-only because it names the fixture's own 30 USD of ledger history.
  const services = createFakeConsoleServices();
  const { owner, operator } = services.sessions;
  const { orgId } = services.ids;
  const grant = {
    target_org_id: orgId,
    amount: "999999999999.99999999" as never,
    kind: "promotional" as const,
    reason: "big",
    idempotency_key: "big-1",
  };

  const before = await services.ledger(owner, { limit: MAX_PAGE_LIMIT });
  const beforeBalance = await services.balances(owner);
  assert.ok(before.ok && beforeBalance.ok);
  const beforeIds = before.value.items.map((entry) => entry.id);

  for (const attempt of [1, 2]) {
    const result = await services.adminGrant(operator, grant);
    assert.equal(result.ok, false, `attempt ${attempt} must be refused`);
    assert.ok(!result.ok && result.error.code === "invalid_request", `attempt ${attempt} code`);
    const after = await services.ledger(owner, { limit: MAX_PAGE_LIMIT });
    assert.ok(after.ok);
    assert.deepEqual(after.value.items.map((entry) => entry.id), beforeIds, `attempt ${attempt} wrote a row`);
  }

  const balance = await services.balances(owner);
  assert.ok(balance.ok, "balances must still work after a refused grant");
  assert.deepEqual(balance.value, beforeBalance.value, "the wallet is exactly as it was");
  const orgsPage = await services.adminOrgs(operator, {});
  assert.ok(orgsPage.ok, "adminOrgs must still work after a refused grant");

  // And a grant that does fit still lands exactly once under its key.
  const fits = await services.adminGrant(operator, { ...grant, amount: "1.00000000" as never, idempotency_key: "big-2" });
  assert.ok(fits.ok && fits.value.replayed === false);
  const replay = await services.adminGrant(operator, { ...grant, amount: "1.00000000" as never, idempotency_key: "big-2" });
  assert.ok(replay.ok && replay.value.replayed === true && replay.value.grant_id === fits.value.grant_id);
});

test("many submissions never reuse a feedback id, and two organizations never share one", async () => {
  // Round 2 non-blocking finding: minted ids came from a global counter with no namespace, so the
  // 187th submission collided with a seeded id. 200 submissions is well past that point.
  const services = createFakeConsoleServices();
  const seen = new Set<string>();
  for (let i = 0; i < 200; i += 1) {
    const entry = await services.feedback.submit(services.sessions.owner, {
      request_id: services.ids.availableRequestId,
      name: "comment",
      value: `note ${i}`,
      idempotency_key: `stress-${i}`,
    });
    assert.ok(entry.ok, `submission ${i}`);
    assert.ok(!seen.has(entry.value.id), `submission ${i} reused id ${entry.value.id}`);
    seen.add(entry.value.id);
  }
  const listed = await services.feedback.list(services.sessions.owner, services.ids.availableRequestId);
  assert.ok(listed.ok);
  assert.equal(new Set(listed.value.map((entry) => entry.id)).size, listed.value.length, "listed ids are unique");
  assert.equal(listed.value.length, 200 + 1, "the seeded entry on this trace plus the 200 minted ones");

  const other = await services.feedback.submit(services.sessions.otherOwner, {
    request_id: services.ids.otherOrgRequestId,
    name: "thumb",
    value: true,
    idempotency_key: "other-org-1",
  });
  assert.ok(other.ok);
  assert.ok(!seen.has(other.value.id), "the second organization must not reuse a feedback id");
});

test("every operation can be made to fail on demand", async () => {
  const services = createFakeConsoleServices();
  const { owner, operator } = services.sessions;
  const { availableRequestId, otherOrgId, keyId } = services.ids;

  const calls: Record<string, () => Promise<{ ok: boolean }>> = {
    usage: () => services.usage(owner, {}),
    usageSummary: () => services.usageSummary(owner, {}),
    usageDaily: () => services.usageDaily(owner, {}),
    balances: () => services.balances(owner),
    ledger: () => services.ledger(owner, {}),
    traces: () => services.traces(owner, {}),
    traceDetail: () => services.traceDetail(owner, availableRequestId),
    traceContent: () => services.traceContent(owner, availableRequestId),
    "feedback.list": () => services.feedback.list(owner, availableRequestId),
    "feedback.submit": () =>
      services.feedback.submit(owner, {
        request_id: availableRequestId,
        name: "thumb",
        value: true,
        idempotency_key: "injection-probe-feedback",
      }),
    "settings.get": () => services.settings.get(owner),
    "settings.update": () => services.settings.update(owner, { trace_mode: "minimal" }),
    "keys.list": () => services.keys.list(owner),
    "keys.create": () => services.keys.create(owner, { name: "injected" }),
    "keys.revoke": () => services.keys.revoke(owner, keyId),
    adminOrgs: () => services.adminOrgs(operator, {}),
    adminGrant: () =>
      services.adminGrant(operator, {
        target_org_id: otherOrgId,
        amount: "1.00000000" as never,
        kind: "promotional",
        reason: "injection probe",
        idempotency_key: "injection-probe",
      }),
    judgeRuns: () => services.judgeRuns(owner, {}),
  };
  assert.deepEqual(Object.keys(calls).sort(), [...CONSOLE_OPERATIONS].sort(), "every operation must be covered");

  for (const operation of CONSOLE_OPERATIONS) {
    services.failNext(operation, "dependency_unavailable");
    const failed = await calls[operation]();
    assert.equal(failed.ok, false, `${operation} ignored the injected failure`);
    const recovered = await calls[operation]();
    assert.equal(recovered.ok, true, `${operation} did not recover after one injected failure`);
  }
});
