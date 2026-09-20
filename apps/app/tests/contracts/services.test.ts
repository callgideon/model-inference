// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// The shared conformance suite applied to the fixture-backed fake, plus the cases that
// only a fake can exercise (failure injection, determinism). C runs the same
// `runConsoleServicesConformance` call against the real services in tests/c/.
import assert from "node:assert/strict";
import test from "node:test";
import {
  assertProbesCoverEveryOperation,
  operationProbes,
  runConsoleServicesConformance,
} from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import { CONSOLE_OPERATIONS, OPERATOR_ONLY_OPERATIONS } from "../../lib/contracts/services.ts";
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
  // Both organizations submit the same number of times, so an id that left the organization out
  // of its namespace collides here rather than in production.
  for (const [session, requestId, who] of [
    [services.sessions.owner, services.ids.availableRequestId, "first organization"],
    [services.sessions.otherOwner, services.ids.otherOrgRequestId, "second organization"],
  ] as [(typeof services)["sessions"]["owner"], string, string][]) {
    for (let i = 0; i < 200; i += 1) {
      const entry = await services.feedback.submit(session, {
        request_id: requestId,
        name: "comment",
        value: `note ${i}`,
        idempotency_key: `stress-${who}-${i}`,
      });
      assert.ok(entry.ok, `${who} submission ${i}`);
      assert.ok(!seen.has(entry.value.id), `${who} submission ${i} reused id ${entry.value.id}`);
      seen.add(entry.value.id);
    }
  }
  assert.equal(seen.size, 400, "400 submissions, 400 ids");

  const listed = await services.feedback.list(services.sessions.owner, services.ids.availableRequestId);
  assert.ok(listed.ok);
  assert.equal(new Set(listed.value.map((entry) => entry.id)).size, listed.value.length, "listed ids are unique");
  assert.equal(listed.value.length, 200 + 1, "the seeded entry on this trace plus the 200 minted ones");
  for (const entry of listed.value) {
    assert.equal(entry.author_role, "customer", "nothing submitted through the console is operator-authored");
    assert.equal(entry.calibration_set, false, "and nothing submitted enrols itself for calibration");
  }
  for (const entry of listed.value) assert.match(entry.id, /^fb_[0-9a-f]{12}$/, "feedback ids stay opaque");
});

test("the fake's own cursor shape cannot be forged", async () => {
  // These were in the exported suite until R36: they read the cursor's structure, so they belong to
  // the implementation that chose that structure. C's cursors will look nothing like this.
  const services = createFakeConsoleServices();
  const owner = services.sessions.owner;
  const page = await services.usage(owner, { limit: 5 });
  assert.ok(page.ok && page.value.next_cursor !== null);
  const decoded = JSON.parse(atob(page.value.next_cursor)) as Record<string, unknown>;
  assert.deepEqual(Object.keys(decoded).sort(), ["a", "b", "k"], "the fake's cursor is {a, b, k}");

  const forge = (fields: Record<string, unknown>): string => btoa(JSON.stringify({ ...decoded, ...fields }));
  // Note what is *not* here: swapping the id for another row's is a well-formed key, so it resumes
  // elsewhere in the order rather than failing. The fake's cursor is tamper-evident for scope, not
  // tamper-proof; C should sign its cursors, and the exported suite requires only the black-box
  // behaviour that both have.
  for (const [what, cursor] of [
    ["a forged scope hash", forge({ k: "deadbeef" })],
    ["a missing tiebreak", btoa(JSON.stringify({ a: decoded.a, k: decoded.k }))],
    ["a numeric key", forge({ a: 0 })],
    ["an empty key", forge({ a: "" })],
    ["not a cursor at all", btoa("{}")],
    ["garbage", "not-base64-at-all"],
  ] as [string, string][]) {
    const result = await services.usage(owner, { limit: 5, cursor });
    assert.ok(!result.ok, `${what} must be refused`);
    assert.equal(result.error.code, "invalid_cursor", what);
  }
  // And the untouched cursor still works, so the forgeries above are not failing for another reason.
  const reissued = await services.usage(owner, { limit: 5, cursor: page.value.next_cursor });
  assert.ok(reissued.ok, "the genuine cursor still resumes");
});

test("a failure after the write replays instead of repeating, for every mutating operation", async () => {
  // The Python FailurePlan's crash-after-commit: the effect and its idempotency record are
  // committed together and then the response is lost. The retry the client makes must find the
  // original effect, not make a second one (N7).
  const services = createFakeConsoleServices();
  const { owner, operator } = services.sessions;
  const { orgId, otherOrgId, availableRequestId } = services.ids;

  const ledgerIds = async (): Promise<string[]> => {
    const page = await services.ledger(owner, { limit: 100 });
    assert.ok(page.ok);
    return page.value.items.map((entry) => entry.id);
  };
  const feedbackIds = async (): Promise<string[]> => {
    const list = await services.feedback.list(owner, availableRequestId);
    assert.ok(list.ok);
    return list.value.map((entry) => entry.id);
  };
  const keyIds = async (): Promise<string[]> => {
    const list = await services.keys.list(owner);
    assert.ok(list.ok);
    return list.value.map((key) => key.id);
  };

  // adminGrant
  services.failNext("adminGrant", "dependency_unavailable", "lost response", "after_write");
  const grantInput = {
    target_org_id: orgId,
    amount: "2.00000000" as never,
    kind: "promotional" as const,
    reason: "crash after commit",
    idempotency_key: "crash-grant-1",
  };
  const lostGrant = await services.adminGrant(operator, grantInput);
  assert.equal(lostGrant.ok, false, "the response is lost");
  const afterGrant = await ledgerIds();
  const retriedGrant = await services.adminGrant(operator, grantInput);
  assert.ok(retriedGrant.ok && retriedGrant.value.replayed === true, "the retry replays the committed grant");
  assert.deepEqual(await ledgerIds(), afterGrant, "the retry must not grant a second time");
  // The committed row carries the key as its `ref`, so a store that lost the record can still
  // recognise its own grant.
  const page = await services.ledger(owner, { limit: 100 });
  assert.ok(page.ok);
  assert.equal(
    page.value.items.filter((entry) => entry.ref === "crash-grant-1").length,
    1,
    "exactly one ledger row carries the idempotency key",
  );

  // feedback.submit
  services.failNext("feedback.submit", "dependency_unavailable", "lost response", "after_write");
  const feedbackInput = {
    request_id: availableRequestId,
    name: "thumb" as const,
    value: true,
    idempotency_key: "crash-feedback-1",
  };
  assert.equal((await services.feedback.submit(owner, feedbackInput)).ok, false);
  const afterFeedback = await feedbackIds();
  const retriedFeedback = await services.feedback.submit(owner, feedbackInput);
  assert.ok(retriedFeedback.ok);
  assert.deepEqual(await feedbackIds(), afterFeedback, "the retry must not record a second signal");

  // keys.create — and the retry must still not hand over the secret
  services.failNext("keys.create", "dependency_unavailable", "lost response", "after_write");
  const keyInput = { name: "crash key", idempotency_key: "crash-key-1" };
  assert.equal((await services.keys.create(owner, keyInput)).ok, false);
  const afterCreate = await keyIds();
  const retriedCreate = await services.keys.create(owner, keyInput);
  assert.ok(retriedCreate.ok);
  assert.equal(retriedCreate.value.secret, null, "a lost first response does not entitle the retry to the secret");
  assert.equal(retriedCreate.value.replayed, true);
  assert.deepEqual(await keyIds(), afterCreate, "the retry must not mint a second key");

  // keys.revoke
  const active = (await services.keys.list(owner));
  assert.ok(active.ok);
  const target = active.value.find((key) => key.revoked_at === null);
  assert.ok(target !== undefined);
  services.failNext("keys.revoke", "dependency_unavailable", "lost response", "after_write");
  assert.equal((await services.keys.revoke(owner, target.id, "crash-revoke-1")).ok, false);
  const retriedRevoke = await services.keys.revoke(owner, target.id, "crash-revoke-1");
  assert.ok(retriedRevoke.ok && retriedRevoke.value.revoked_at !== null, "the retry returns the revocation");

  // settings.update — the audit trail must not gain a second entry
  services.failNext("settings.update", "dependency_unavailable", "lost response", "after_write");
  const settingsInput = { evaluation_consent: false, idempotency_key: "crash-settings-1" };
  assert.equal((await services.settings.update(owner, settingsInput)).ok, false);
  const afterSettings = await services.settings.get(owner);
  assert.ok(afterSettings.ok);
  const retriedSettings = await services.settings.update(owner, settingsInput);
  assert.ok(retriedSettings.ok);
  assert.deepEqual(retriedSettings.value, afterSettings.value, "the retry changes nothing further");

  // the two operator writes
  services.failNext("adminSetSuspension", "dependency_unavailable", "lost response", "after_write");
  const suspensionInput = {
    target_org_id: otherOrgId,
    suspended: true,
    reason: "crash after commit",
    idempotency_key: "crash-suspend-1",
  };
  assert.equal((await services.adminSetSuspension(operator, suspensionInput)).ok, false);
  const retriedSuspension = await services.adminSetSuspension(operator, suspensionInput);
  assert.ok(retriedSuspension.ok && retriedSuspension.value.suspended === true);

  services.failNext("calibration.label", "dependency_unavailable", "lost response", "after_write");
  const labelInput = {
    request_id: availableRequestId,
    rubric_version: 2,
    label: "correct" as const,
    idempotency_key: "crash-label-1",
  };
  assert.equal((await services.calibration.label(operator, labelInput)).ok, false);
  const labels = await services.calibration.list(operator, { limit: 100 });
  assert.ok(labels.ok);
  const committed = labels.value.items.filter((entry) => entry.request_id === availableRequestId).length;
  const retriedLabel = await services.calibration.label(operator, labelInput);
  assert.ok(retriedLabel.ok);
  const after = await services.calibration.list(operator, { limit: 100 });
  assert.ok(after.ok);
  assert.equal(
    after.value.items.filter((entry) => entry.request_id === availableRequestId).length,
    committed,
    "the retry must not add a second label",
  );
});

test("a key secret is retained nowhere in the fake's state", async () => {
  // The portable half of this lives in the conformance suite (no operation returns the secret).
  // This is the part only a fake can prove: it is not in the state at all, so no future operation
  // and no authorization mistake can produce it (B1/R16).
  const services = createFakeConsoleServices();
  const created = await services.keys.create(services.sessions.owner, {
    name: "deep scan",
    idempotency_key: "deep-scan-1",
  });
  assert.ok(created.ok && typeof created.value.secret === "string");
  const secret = created.value.secret;
  assert.ok(secret.length > 20, "the fixture secret is long enough to be worth hiding");

  const dumped = JSON.stringify(services.unsafeDebugState());
  assert.ok(!dumped.includes(secret), "the secret is somewhere in the fake's state");
  // The prefix is public and must still be there, so the scan above is not passing by accident.
  assert.ok(dumped.includes(created.value.prefix), "the key's public prefix is part of the state");
  await services.keys.revoke(services.sessions.owner, created.value.id);
  assert.ok(
    !JSON.stringify(services.unsafeDebugState()).includes(secret),
    "revocation must not resurrect the secret either",
  );
});

test("every operation can be made to fail on demand, and recovers on the next call", async () => {
  const services = createFakeConsoleServices();
  // The probe table is the conformance suite's, so an operation cannot be added without appearing
  // here — the old hand-kept list had to be remembered, and was not.
  const probes = operationProbes(services, services.sessions, services.ids);
  assertProbesCoverEveryOperation(probes);

  for (const operation of CONSOLE_OPERATIONS) {
    const probe = probes[operation];
    const session = (OPERATOR_ONLY_OPERATIONS as readonly string[]).includes(operation)
      ? services.sessions.operator
      : services.sessions.owner;
    services.failNext(operation, "dependency_unavailable");
    const failed = await probe.call(session);
    assert.equal(failed.ok, false, `${operation} ignored the injected failure`);
    const recovered = await probe.call(session);
    assert.equal(recovered.ok, true, `${operation} did not recover after one injected failure`);
  }
});
