// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// R36: the exported conformance suite must treat a cursor as opaque, so an implementation that
// signs, encrypts or otherwise reshapes its cursors passes unchanged. The proof is this file: the
// whole suite runs against a wrapper that rewrites every `next_cursor` into a form the fake cannot
// parse, and rewrites it back on the way in. If any case inspected a cursor's structure, or forged
// one by rebuilding it, the suite would fail here.
import assert from "node:assert/strict";
import test from "node:test";
import {
  runConsoleServicesConformance,
  type ConsoleHarness,
} from "../../lib/contracts/conformance.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import type { ConsoleServices } from "../../lib/contracts/services.ts";
import type { Page, Result } from "../../lib/contracts/types.ts";

/** Stands for a signature or an encryption envelope: opaque to the caller, meaningless to the fake. */
const WRAPPER_PREFIX = "v1.";

function wrapCursor(cursor: string | null): string | null {
  return cursor === null ? null : `${WRAPPER_PREFIX}${cursor.split("").reverse().join("")}`;
}

function unwrapCursor(cursor: string | null | undefined): string | null | undefined {
  if (cursor === undefined || cursor === null || cursor === "") return cursor;
  if (!cursor.startsWith(WRAPPER_PREFIX)) return "not-a-cursor-from-this-service";
  return cursor.slice(WRAPPER_PREFIX.length).split("").reverse().join("");
}

function wrapQuery<Q extends { cursor?: string | null }>(query: Q): Q {
  // A caller may hand any rubbish through this wrapper — the suite checks that too — so anything
  // that is not an object passes straight to the implementation to refuse.
  if (typeof query !== "object" || query === null || !("cursor" in query)) return query;
  return { ...query, cursor: unwrapCursor(query.cursor) };
}

async function wrapPage<T>(promise: Promise<Result<Page<T>>>): Promise<Result<Page<T>>> {
  const result = await promise;
  if (!result.ok) return result;
  return { ok: true, value: { ...result.value, next_cursor: wrapCursor(result.value.next_cursor) } };
}

/** The same services with opaque cursors. Every other operation passes straight through. */
function withOpaqueCursors(inner: ConsoleServices): ConsoleServices {
  return {
    usage: (session, query) => wrapPage(inner.usage(session, wrapQuery(query))),
    usageSummary: (session, query) => inner.usageSummary(session, wrapQuery(query)),
    usageDaily: (session, query) => inner.usageDaily(session, wrapQuery(query)),
    balances: (session) => inner.balances(session),
    ledger: (session, query) => wrapPage(inner.ledger(session, wrapQuery(query))),
    traces: (session, query) => wrapPage(inner.traces(session, wrapQuery(query))),
    traceDetail: (session, requestId) => inner.traceDetail(session, requestId),
    traceContent: (session, requestId) => inner.traceContent(session, requestId),
    feedback: {
      list: (session, requestId) => inner.feedback.list(session, requestId),
      submit: (session, input) => inner.feedback.submit(session, input),
    },
    calibration: {
      label: (session, input) => inner.calibration.label(session, input),
      list: (session, query) => wrapPage(inner.calibration.list(session, wrapQuery(query))),
    },
    settings: {
      get: (session) => inner.settings.get(session),
      update: (session, update) => inner.settings.update(session, update),
    },
    keys: {
      list: (session) => inner.keys.list(session),
      create: (session, input) => inner.keys.create(session, input),
      revoke: (session, keyId, idempotencyKey) => inner.keys.revoke(session, keyId, idempotencyKey),
    },
    adminOrgs: (session, query) => wrapPage(inner.adminOrgs(session, wrapQuery(query))),
    adminGrant: (session, input) => inner.adminGrant(session, input),
    adminSetSuspension: (session, input) => inner.adminSetSuspension(session, input),
    adminSetEntitlements: (session, input) => inner.adminSetEntitlements(session, input),
    adminAudit: (session, query) => wrapPage(inner.adminAudit(session, wrapQuery(query))),
    judgeRuns: (session, query) => wrapPage(inner.judgeRuns(session, wrapQuery(query))),
  };
}

test("the wrapper really does make cursors unreadable to the implementation", async () => {
  const fake = createFakeConsoleServices();
  const wrapped = withOpaqueCursors(fake);
  const page = await wrapped.usage(fake.sessions.owner, { limit: 5 });
  assert.ok(page.ok && page.value.next_cursor !== null);
  const cursor = page.value.next_cursor;
  assert.ok(cursor.startsWith(WRAPPER_PREFIX), "the cursor is in the wrapper's own form");
  assert.throws(
    () => JSON.parse(atob(cursor)),
    "a suite that decoded this cursor could not work against the wrapper",
  );
  // Handed back to the underlying fake directly, it is not a cursor at all.
  const direct = await fake.usage(fake.sessions.owner, { limit: 5, cursor });
  assert.ok(!direct.ok && direct.error.code === "invalid_cursor", "the fake cannot read the wrapped form");
  // Through the wrapper it resumes normally.
  const next = await wrapped.usage(fake.sessions.owner, { limit: 5, cursor });
  assert.ok(next.ok && next.value.items.length > 0, "and the wrapper resumes the walk");
});

runConsoleServicesConformance((): ConsoleHarness => {
  const fake = createFakeConsoleServices();
  return { services: withOpaqueCursors(fake), sessions: fake.sessions, ids: fake.ids };
}, "ConsoleServices with opaque cursors");
