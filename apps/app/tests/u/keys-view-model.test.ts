// node --test "tests/**/*.test.ts"
//
// U2: the API Keys page model (`app/(console)/api-keys/view-model.ts`). Every state the page renders
// is decided here from C0's consumer context and C0's `reads.keys()` result, and every outcome the
// create dialog shows from C3A's `createConsumerKey` result. Failure oracles are named per case.
import assert from "node:assert/strict";
import test from "node:test";

import type { ApiKeyCreated, ApiKeySummary, Result } from "../../lib/contracts/types.ts";
import {
  LOST_KEY_COPY,
  ONE_TIME_COPY,
  REPLAYED_COPY,
  REVOCATION_COPY,
  createOutcome,
  keysPageModel,
  revokeConfirmText,
} from "../../app/(console)/api-keys/view-model.ts";

const ACCOUNT = { userId: "u", email: "a@example.com", walletId: "w", orgId: "o", suspended: false };
const READY = { state: "ready" as const, account: ACCOUNT };
const SUSPENDED = { state: "ready" as const, account: { ...ACCOUNT, suspended: true } };
const NOT_READY = [
  { state: "signed_out" as const },
  { state: "unverified" as const, userId: "u", email: "a@example.com" },
  { state: "onboarding" as const, userId: "u", email: "a@example.com" },
  { state: "unavailable" as const },
];

const key = (over: Partial<ApiKeySummary> = {}): ApiKeySummary => ({
  id: "11111111-1111-4111-8111-111111111111",
  name: "production",
  prefix: "sk-infrx-AbCd1234",
  created_at: "2026-09-25T10:15:30.123456Z",
  last_used_at: null,
  revoked_at: null,
  trace_mode: null,
  ...over,
});
const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const down: Result<ApiKeySummary[]> = { ok: false, error: { code: "dependency_unavailable", message: "your account data could not be read right now; try again" } };

export const T = {
  gate: "U2-K01 only a ready, unsuspended individual is offered key creation, and every other state says why",
  failed: "U2-K02 a failed or missing key read is an unavailable state with a retry, never 'No keys yet'",
  rows: "U2-K03 a row shows the stored prefix only, UTC times and whether it is revoked; only an active key is revocable",
  suspended: "U2-K04 a suspended individual keeps the list and can still revoke (R33)",
  once: "U2-K05 the plaintext is shown only for a first, non-replayed creation; a replay or a failure never shows one",
  copy: "U2-K06 revocation and lost-key copy is the decided public text (P-26), never 'within a minute'",
};

test(T.gate, () => {
  assert.deepEqual(keysPageModel(READY, ok([])).create, { allowed: true });
  const reasons = new Set<string>();
  for (const context of [...NOT_READY, SUSPENDED]) {
    const model = keysPageModel(context, context.state === "ready" ? ok([]) : null);
    assert.equal(model.create.allowed, false, `${context.state} must not be offered creation`);
    if (!model.create.allowed) reasons.add(model.create.reason);
  }
  assert.equal(reasons.size, NOT_READY.length + 1, "each state has its own reason");
  const unverified = keysPageModel(NOT_READY[1], null).create;
  assert.match(unverified.allowed ? "" : unverified.reason, /verify your email/i);
});

test(T.failed, () => {
  for (const list of [keysPageModel(READY, down).list, keysPageModel(READY, null).list, keysPageModel(NOT_READY[3], null).list]) {
    assert.equal(list.kind, "unavailable");
    if (list.kind === "unavailable") assert.equal(list.retry, true, "a transient failure offers a retry");
  }
  const bad = keysPageModel(READY, down).list;
  assert.doesNotMatch(bad.kind === "unavailable" ? bad.message : "", /no keys/i);
  // Not-yet-ready accounts are told what to do, and a retry would not help them.
  for (const context of NOT_READY.slice(1, 3)) {
    const list = keysPageModel(context, null).list;
    assert.equal(list.kind, "unavailable");
    if (list.kind === "unavailable") assert.equal(list.retry, false);
  }
  const empty = keysPageModel(READY, ok([])).list;
  assert.equal(empty.kind, "empty");
});

test(T.rows, () => {
  const model = keysPageModel(
    READY,
    ok([key(), key({ id: "22222222-2222-4222-8222-222222222222", name: "old", last_used_at: "2026-09-25T11:00:00Z", revoked_at: "2026-09-25T12:30:59.5Z" })]),
  );
  assert.equal(model.list.kind, "ready");
  if (model.list.kind !== "ready") return;
  const [active, revoked] = model.list.rows;
  assert.equal(active.prefix, "sk-infrx-AbCd1234…");
  assert.equal(active.created, "2026-09-25 10:15 UTC");
  assert.equal(active.lastUsed, "Never");
  assert.equal(active.revoked, null);
  assert.equal(active.revocable, true);
  assert.equal(revoked.lastUsed, "2026-09-25 11:00 UTC");
  assert.equal(revoked.revoked, "2026-09-25 12:30 UTC");
  assert.equal(revoked.revocable, false, "a revoked key offers no second revoke");
  assert.deepEqual(Object.keys(active).sort(), ["created", "id", "lastUsed", "name", "prefix", "revocable", "revoked"]);
});

test(T.suspended, () => {
  const model = keysPageModel(SUSPENDED, ok([key()]));
  assert.equal(model.create.allowed, false);
  if (!model.create.allowed) assert.match(model.create.reason, /suspended/i);
  assert.equal(model.list.kind, "ready");
  if (model.list.kind === "ready") assert.equal(model.list.rows[0].revocable, true, "a leaked key can always be revoked");
});

test(T.once, () => {
  const created = (over: Partial<ApiKeyCreated>): Result<ApiKeyCreated> => ok({ ...key(), secret: "sk-infrx-" + "x".repeat(40), replayed: false, ...over });
  assert.deepEqual(createOutcome(created({})), { kind: "secret", secret: "sk-infrx-" + "x".repeat(40) });
  assert.deepEqual(createOutcome(created({ secret: null, replayed: true })), { kind: "notice", message: REPLAYED_COPY });
  // A replay that somehow carried a secret still does not show it: only the first response may.
  assert.deepEqual(createOutcome(created({ replayed: true })), { kind: "notice", message: REPLAYED_COPY });
  assert.deepEqual(createOutcome(created({ secret: null })), { kind: "notice", message: REPLAYED_COPY });
  const refused = createOutcome({ ok: false, error: { code: "forbidden", message: "verify your email address first" } });
  assert.deepEqual(refused, { kind: "notice", message: "verify your email address first" });
});

test(T.copy, () => {
  assert.equal(
    REVOCATION_COPY,
    "Revoking a key stops new requests immediately. Reads and cancels by that key stop within 60 seconds while our account service is reachable. During an account-service outage, a revoked key may continue to read or cancel its own existing jobs until the service recovers. It can never start new work.",
  );
  assert.match(ONE_TIME_COPY, /only time/i);
  assert.match(ONE_TIME_COPY, /cannot be shown again/i);
  assert.match(LOST_KEY_COPY, /create a new key/i);
  assert.match(LOST_KEY_COPY, /revoke the old/i);
  assert.match(REPLAYED_COPY, /cannot be shown again/i);
  const confirm = revokeConfirmText("production");
  assert.match(confirm, /"production"/);
  assert.match(confirm, /immediately/);
  assert.match(confirm, /cannot be undone/i);
  assert.doesNotMatch(confirm, /within a minute/);
});
