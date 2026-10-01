// node --test "tests/**/*.test.ts"
//
// UX-04 (C-02, UX-T03 fixture half): the /models first-call guide is decided from the account's own
// scoped reads - C0's keys() through U2's keysPageModel, and the first page of requests(). A brand-new
// account gets "Make your first request" with Create key as step one; an account with an active key is
// offered that key and never forced to create another; completion is a persisted succeeded request in
// that read, never a click or a browser flag; a bounded page without a success claims nothing.
//
// Oracles: a guide that treats a revoked key as usable, hides Create key from a new account, offers it
// to a refused account, claims completion from a failed/running request, or claims "no request yet"
// from a page that has more behind its cursor or from a failed read, fails here.
import assert from "node:assert/strict";
import test from "node:test";
import { keysPageModel } from "../../../app/(console)/api-keys/view-model.ts";
import { FIRST_REQUEST, QUICKSTART, firstCallModel } from "../../../app/(console)/models/first-call.ts";
import type { ApiKeySummary, Page, Result } from "../../../lib/contracts/types.ts";
import type { ConsumerRequest } from "../../../lib/contracts/v2/consumer.ts";

const READY = { state: "ready", account: { suspended: false } };
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const down = { ok: false as const, error: { code: "dependency_unavailable" as const, message: "try again" } };

function key(name: string, revoked = false): ApiKeySummary {
  return {
    id: `00000000-0000-4000-8000-${name.padStart(12, "0").slice(-12)}`,
    name,
    prefix: `sk-infrx-${name.slice(0, 4)}`,
    created_at: "2026-09-30T10:00:00Z",
    last_used_at: null,
    revoked_at: revoked ? "2026-09-30T11:00:00Z" : null,
    trace_mode: null,
  };
}

function request(id: number, state: string): ConsumerRequest {
  return { request_id: `10000000-0000-4000-8000-${String(id).padStart(12, "0")}`, state } as ConsumerRequest;
}

const page = (items: ConsumerRequest[], next_cursor: string | null = null): Result<Page<ConsumerRequest>> => ok({ items, next_cursor });
const guide = (keys: ApiKeySummary[] | null, requests: Result<Page<ConsumerRequest>> | null, context: { state: string; account?: { suspended: boolean } } = READY) =>
  firstCallModel(keysPageModel(context, keys === null ? down : ok(keys)), requests);

test("a brand-new account gets the first-request guide with Create key as step one", () => {
  const g = guide([], page([]));
  assert.equal(g.heading, FIRST_REQUEST);
  assert.deepEqual(g.key, { kind: "create" });
  assert.equal(g.canCreate, true);
  assert.equal(g.done, null);
  assert.equal(g.open, true);
});

test("an account with an active key is offered it and not forced to create another", () => {
  const g = guide([key("robotics"), key("old", true)], page([]));
  assert.deepEqual(g.key, { kind: "existing", keys: [{ name: "robotics", prefix: "sk-infrx-robo…" }] });
  assert.equal(g.canCreate, true, "creating another stays optional");
  assert.equal(g.heading, FIRST_REQUEST, "no request yet, whatever the keys");
});

test("a revoked key is not an active key: the account is asked to create one", () => {
  const g = guide([key("old", true)], page([]));
  assert.deepEqual(g.key, { kind: "create" });
});

test("completion is a persisted succeeded request in the account's own read, linked by its id", () => {
  const g = guide([key("robotics")], page([request(3, "failed"), request(2, "succeeded"), request(1, "succeeded")], "cursor"));
  assert.deepEqual(g.done, { requestId: request(2, "succeeded").request_id });
  assert.equal(g.heading, QUICKSTART);
  assert.equal(g.open, false, "the dominant guide gives way to a compact Quickstart");
});

test("a request that has not succeeded is not completion", () => {
  for (const state of ["queued", "running", "failed", "cancelled", "expired", "preparing", "unknown"]) {
    const g = guide([key("robotics")], page([request(1, state)]));
    assert.equal(g.done, null, state);
    assert.equal(g.open, true, state);
  }
});

test("a bounded page without a success claims nothing: Quickstart, open, no completion", () => {
  const g = guide([key("robotics")], page([request(1, "failed")], "more-behind-this-cursor"));
  assert.equal(g.heading, QUICKSTART);
  assert.equal(g.done, null);
  assert.equal(g.open, true);
  assert.equal(guide([key("robotics")], page([request(1, "failed")])).heading, FIRST_REQUEST, "an exhaustive page with no success is no success");
});

test("an unavailable read claims nothing either way", () => {
  const noRequests = guide([], down);
  assert.equal(noRequests.heading, QUICKSTART);
  assert.equal(noRequests.done, null);
  assert.equal(guide([], null).heading, QUICKSTART);
  const noKeys = guide(null, page([]));
  assert.deepEqual(noKeys.key, { kind: "unavailable" });
  assert.equal(noKeys.canCreate, true);
});

test("an account that may not create a key is told why, not shown a button", () => {
  for (const context of [{ state: "ready", account: { suspended: true } }, { state: "unverified" }, { state: "unavailable" }]) {
    const g = guide([], page([]), context);
    assert.equal(g.canCreate, false, context.state);
    assert.equal(g.key.kind, "refused", context.state);
    assert.ok(g.key.kind === "refused" && g.key.reason.length > 0);
  }
});
