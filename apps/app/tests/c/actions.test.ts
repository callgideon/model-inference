// node --test "tests/**/*.test.ts"
//
// C3A: the trusted consumer actions (CONSOLE-FLOWS, DUR-RLS, CREDIT-SPEND's "no optimistic success").
//
// The decisions under test, each with the defect it catches:
// - the tenant, the creator, the audience and the grant's individual come from the server-resolved
//   consumer context, never from the input: a smuggled org/role/audience/amount/actor is refused;
// - only a verified individual with a granted wallet may mint a key; a suspended one may still revoke;
// - the key plaintext exists only in the first response; a replay (double-click, retry) never mints a
//   second key and never shows a secret; a lost response is revoke-and-recreate, not redisplay;
// - revocation is idempotent and tenant-scoped (another tenant's key is `not_found`, not revoked);
// - the grant is the DB's one idempotent entitlement for the session's own user; denials are answers
//   with fixed text that enumerate nothing; an outage is never a grant;
// - the DB's own error text never reaches a caller; a write whose outcome is unknown says so;
// - operator actions need operator authority, a reason and an idempotency key, and the audit actor is
//   the session, never the form.
//
// The same actions run against real PostgREST in `actions-postgrest.test.ts`.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import type { Result } from "../../lib/contracts/types.ts";
import type { ConsumerAccount, ConsumerContext, RpcClient } from "../../lib/services/console.ts";
import {
  SIGNUP_CAMPAIGN,
  createConsumerActions,
  operatorCommand,
  runOperatorCommand,
  sameOrigin,
  type KeyStore,
  type OperatorPort,
} from "../../lib/services/actions.ts";

const ME = "c1000000-0000-4000-8000-000000000001";
const OTHER = "c1000000-0000-4000-8000-000000000002";
const MY_ORG = "0e000000-0000-4000-8000-000000000001";
const MY_WALLET = "aaaaaaaa-0000-4000-8000-000000000001";
const KEY = "c7000000-0000-4000-8000-000000000001";
const AT = "2026-09-25T12:00:00.000Z";

const account: ConsumerAccount = { userId: ME, email: "me@example.com", walletId: MY_WALLET, orgId: MY_ORG, suspended: false };
const ready: ConsumerContext = { state: "ready", account };
const suspended: ConsumerContext = { state: "ready", account: { ...account, suspended: true } };
const onboarding: ConsumerContext = { state: "onboarding", userId: ME, email: "me@example.com" };
const unverified: ConsumerContext = { state: "unverified", userId: ME, email: "me@example.com" };
const NOT_READY: [string, ConsumerContext, string][] = [
  ["signed out", { state: "signed_out" }, "forbidden"],
  ["unverified", unverified, "forbidden"],
  ["onboarding", onboarding, "forbidden"],
  ["unavailable", { state: "unavailable" }, "dependency_unavailable"],
];

function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}

function codeOf(result: Result<unknown>): string {
  return result.ok ? "ok" : result.error.code;
}

type Answer = { data: unknown; error: { code?: string; message?: string } | null };
type StoredKey = { id: string; org_id: string; created_by: string; name: string; prefix: string; key_hash: string; revoked_at: string | null };

/** A key table that behaves like `public.api_keys` under the owner's JWT for the three calls used. */
function memoryStore(options: { failInsert?: Answer["error"]; failRevoke?: Answer["error"]; slow?: boolean } = {}) {
  const rows: StoredKey[] = [
    { id: "c7000000-0000-4000-8000-0000000000f2", org_id: "0e000000-0000-4000-8000-000000000002", created_by: OTHER, name: "theirs", prefix: "sk-infrx-theirs00", key_hash: "x", revoked_at: null },
  ];
  const calls: string[] = [];
  let next = 0;
  const view = (row: StoredKey) => ({
    id: row.id, name: row.name, prefix: row.prefix, created_at: AT, last_used_at: null, revoked_at: row.revoked_at, trace_mode: null,
  });
  const store: KeyStore = {
    async insert(row) {
      calls.push("insert");
      if (options.slow) await new Promise((resolve) => setTimeout(resolve, 5));
      if (options.failInsert) return { data: null, error: options.failInsert };
      const id = next === 0 ? KEY : `c7000000-0000-4000-8000-${String(next).padStart(12, "0")}`;
      next += 1;
      const stored = { id, revoked_at: null, ...row };
      rows.push(stored);
      return { data: [view(stored)], error: null };
    },
    async revoke(orgId, keyId, at) {
      calls.push("revoke");
      if (options.failRevoke) return { data: null, error: options.failRevoke };
      const hit = rows.filter((row) => row.id === keyId && row.org_id === orgId && row.revoked_at === null);
      for (const row of hit) row.revoked_at = at;
      return { data: hit.map(view), error: null };
    },
    async find(orgId, keyId) {
      calls.push("find");
      return { data: rows.filter((row) => row.id === keyId && row.org_id === orgId).map(view), error: null };
    },
  };
  return { store, rows, calls };
}

function rpcOf(handler: (fn: string, args: Record<string, unknown>) => Answer) {
  const calls: [string, Record<string, unknown>][] = [];
  const client: RpcClient = {
    rpc(fn, args) {
      calls.push([fn, args]);
      return Promise.resolve(handler(fn, args));
    },
  };
  return { client, calls };
}

const actions = () => createConsumerActions({ now: () => new Date(AT) });

// ------------------------------------------------------------------------------------ key create

test("key create: minted server-side for the context's own org and user, secret once, hash stored", async () => {
  const { store, rows } = memoryStore();
  const created = valueOf(await actions().createKey(ready, store, { name: "  laptop  " }), "create");
  assert.equal(created.replayed, false);
  assert.match(created.secret ?? "", /^sk-infrx-[A-Za-z0-9]{40}$/);
  const stored = rows[rows.length - 1];
  assert.equal(stored.org_id, MY_ORG);
  assert.equal(stored.created_by, ME);
  assert.equal(stored.name, "laptop");
  assert.equal(stored.prefix, (created.secret ?? "").slice(0, 17));
  assert.equal(stored.key_hash, createHash("sha256").update(created.secret ?? "").digest("hex"));
  assert.ok(!JSON.stringify(stored).includes(created.secret ?? "?"), "the plaintext is never stored");
  assert.deepEqual(Object.keys(stored).sort(), ["created_by", "id", "key_hash", "name", "org_id", "prefix", "revoked_at"]);
});

test("key create: a smuggled org, role, audience, amount or unknown field is refused before any write", async () => {
  for (const smuggled of [
    { name: "k", org_id: OTHER },
    { name: "k", role: "owner" },
    { name: "k", audience: "operator" },
    { name: "k", amount: "10000" },
    { name: "k", created_by: OTHER },
    null,
    "k",
  ]) {
    const { store, calls } = memoryStore();
    const result = await actions().createKey(ready, store, smuggled);
    assert.equal(codeOf(result), "invalid_request", JSON.stringify(smuggled));
    assert.deepEqual(calls, [], "nothing is written for a refused input");
  }
});

test("key create: name and capture validation (capture is not offered to consumers)", async () => {
  for (const [input, code] of [
    [{ name: "" }, "invalid_request"],
    [{ name: "   " }, "invalid_request"],
    [{ name: 7 }, "invalid_request"],
    [{ name: "x".repeat(201) }, "invalid_request"],
    [{ name: "k", trace_mode: "full" }, "unsupported_parameter"],
    [{ name: "k", trace_mode: "minimal" }, "unsupported_parameter"],
    [{ name: "k", idempotency_key: 5 }, "invalid_request"],
    [{ name: "k", idempotency_key: "" }, "invalid_request"],
  ] as const) {
    const { store, calls } = memoryStore();
    assert.equal(codeOf(await actions().createKey(ready, store, input)), code, JSON.stringify(input));
    assert.deepEqual(calls, []);
  }
  const { store } = memoryStore();
  valueOf(await actions().createKey(ready, store, { name: "x".repeat(200), trace_mode: "off" }), "200 chars, capture off");
});

test("key create: only a ready, unsuspended individual may mint a key", async () => {
  for (const [label, context, code] of NOT_READY) {
    const { store, calls } = memoryStore();
    assert.equal(codeOf(await actions().createKey(context, store, { name: "k" })), code, label);
    assert.deepEqual(calls, [], label);
  }
  const { store, calls } = memoryStore();
  assert.equal(codeOf(await actions().createKey(suspended, store, { name: "k" })), "org_suspended");
  assert.deepEqual(calls, []);
});

test("key create: a replay under the same idempotency key never mints twice and never shows a secret", async () => {
  const { store, calls } = memoryStore();
  const act = actions();
  const first = valueOf(await act.createKey(ready, store, { name: "ci", idempotency_key: "dialog-1" }), "first");
  const again = valueOf(await act.createKey(ready, store, { name: "ci", idempotency_key: "dialog-1" }), "replay");
  assert.equal(first.replayed, false);
  assert.ok(first.secret !== null);
  assert.equal(again.replayed, true);
  assert.equal(again.secret, null);
  assert.equal(again.id, first.id);
  assert.equal(calls.filter((call) => call === "insert").length, 1);
  // A replay reads the key's CURRENT state: a revocation since is visible, not the first answer.
  valueOf(await act.revokeKey(ready, store, first.id), "revoke");
  const late = valueOf(await act.createKey(ready, store, { name: "ci", idempotency_key: "dialog-1" }), "late replay");
  assert.equal(late.revoked_at, AT);
  assert.equal(late.secret, null);
});

test("key create: a double-click (concurrent calls) mints one key; only one response carries the secret", async () => {
  const { store, calls } = memoryStore({ slow: true });
  const act = actions();
  const both = await Promise.all([
    act.createKey(ready, store, { name: "ci", idempotency_key: "dialog-2" }),
    act.createKey(ready, store, { name: "ci", idempotency_key: "dialog-2" }),
  ]);
  const values = both.map((result, i) => valueOf(result, `click ${i}`));
  assert.equal(calls.filter((call) => call === "insert").length, 1);
  assert.equal(values.filter((value) => value.secret !== null).length, 1);
  assert.equal(values[0].id, values[1].id);
});

test("key create: an idempotency key is per individual and per payload", async () => {
  const { store, calls } = memoryStore();
  const act = actions();
  valueOf(await act.createKey(ready, store, { name: "ci", idempotency_key: "shared" }), "mine");
  assert.equal(codeOf(await act.createKey(ready, store, { name: "other", idempotency_key: "shared" })), "idempotency_conflict");
  const otherAccount: ConsumerContext = { state: "ready", account: { ...account, userId: OTHER, orgId: "0e000000-0000-4000-8000-000000000002" } };
  const theirs = valueOf(await act.createKey(otherAccount, store, { name: "ci", idempotency_key: "shared" }), "theirs");
  assert.ok(theirs.secret !== null, "another individual's identical key is not a replay of mine");
  assert.equal(calls.filter((call) => call === "insert").length, 2);
});

test("key create: a failed write shows fixed text (never the DB's) and says what to do if it committed", async () => {
  const leak = { code: "23505", message: 'duplicate key value violates unique constraint "api_keys_key_hash_key" (org 0e00...)' };
  const { store } = memoryStore({ failInsert: leak });
  const failed = await actions().createKey(ready, store, { name: "k" });
  assert.equal(codeOf(failed), "dependency_unavailable");
  assert.ok(!failed.ok && !failed.error.message.includes("api_keys"), "no DB text");
  assert.ok(!failed.ok && /revoke/i.test(failed.error.message), "a possibly-committed key is revoked, not redisplayed");
  const { store: denied } = memoryStore({ failInsert: { code: "42501", message: "new row violates row-level security policy for table api_keys" } });
  const refused = await actions().createKey(ready, denied, { name: "k" });
  assert.equal(codeOf(refused), "forbidden");
  assert.ok(!refused.ok && !refused.error.message.includes("api_keys"));
  // A failed attempt is not remembered: the retry under the same key mints (the first wrote nothing).
  const { store: flaky, calls } = memoryStore({ failInsert: leak });
  const act = actions();
  await act.createKey(ready, flaky, { name: "k", idempotency_key: "retry" });
  await act.createKey(ready, flaky, { name: "k", idempotency_key: "retry" });
  assert.equal(calls.filter((call) => call === "insert").length, 2);
});

// ------------------------------------------------------------------------------------ key revoke

test("key revoke: tenant-scoped, idempotent, and allowed while suspended", async () => {
  const { store, rows } = memoryStore();
  const act = actions();
  const created = valueOf(await act.createKey(ready, store, { name: "k" }), "create");
  const revoked = valueOf(await act.revokeKey(suspended, store, created.id), "revoke while suspended");
  assert.equal(revoked.revoked_at, AT);
  const again = valueOf(await act.revokeKey(ready, store, created.id), "revoke again");
  assert.equal(again.revoked_at, AT, "a second revoke answers the first revocation");
  const theirs = rows[0];
  assert.equal(codeOf(await act.revokeKey(ready, store, theirs.id)), "not_found");
  assert.equal(theirs.revoked_at, null, "another tenant's key is untouched");
  assert.equal(codeOf(await act.revokeKey(ready, store, "c7000000-0000-4000-8000-00000000dead")), "not_found");
});

test("key revoke: a malformed id or an unready context never reaches the store", async () => {
  for (const id of ["", "not-a-uuid", 5, null, `${KEY}' or 1=1`]) {
    const { store, calls } = memoryStore();
    assert.equal(codeOf(await actions().revokeKey(ready, store, id)), "not_found", String(id));
    assert.deepEqual(calls, []);
  }
  for (const [label, context, code] of NOT_READY) {
    const { store, calls } = memoryStore();
    assert.equal(codeOf(await actions().revokeKey(context, store, KEY)), code, label);
    assert.deepEqual(calls, []);
  }
  const { store } = memoryStore({ failRevoke: { code: "08006", message: "connection to 10.0.0.5 lost" } });
  const failed = await actions().revokeKey(ready, store, KEY);
  assert.equal(codeOf(failed), "dependency_unavailable");
  assert.ok(!failed.ok && !failed.error.message.includes("10.0.0.5"));
});

// -------------------------------------------------------------------------------- signup grant

const GRANT_ROW = {
  status: "granted",
  user_id: ME,
  wallet_id: MY_WALLET,
  ledger_operation_id: "0b000000-0000-4000-8000-000000000001",
  amount: "10000.00000000",
  granted_at: "2026-09-25T12:00:00+00:00",
};

test("grant: the DB's one entitlement for the SESSION's user, never an input, exact CREDIT string", async () => {
  const { client, calls } = rpcOf(() => ({ data: [GRANT_ROW], error: null }));
  const granted = valueOf(await actions().claimGrant(onboarding, () => client), "claim");
  assert.deepEqual(calls, [["claim_signup_grant", { p_user_id: ME, p_campaign_version: SIGNUP_CAMPAIGN, p_operation_id: null }]]);
  assert.deepEqual(granted, { status: "granted", wallet_id: MY_WALLET, amount: "10000.00000000", granted_at: GRANT_ROW.granted_at });
  const { client: again } = rpcOf(() => ({ data: [{ ...GRANT_ROW, status: "replayed" }], error: null }));
  const replayed = valueOf(await actions().claimGrant(ready, () => again), "replay");
  assert.equal(replayed.status, "already_granted");
  assert.equal(typeof replayed.amount, "string");
});

test("grant: an unverified, signed-out or unknown context never calls the grant", async () => {
  for (const [label, context, code] of [
    ["signed out", { state: "signed_out" }, "forbidden"],
    ["unverified", unverified, "forbidden"],
    ["unavailable", { state: "unavailable" }, "dependency_unavailable"],
  ] as [string, ConsumerContext, string][]) {
    let asked = 0;
    const result = await actions().claimGrant(context, () => {
      asked += 1;
      return rpcOf(() => ({ data: [GRANT_ROW], error: null })).client;
    });
    assert.equal(codeOf(result), code, label);
    assert.equal(asked, 0, `${label}: the service client is not even built`);
  }
});

test("grant: denials are answers with no reason; an outage or a foreign row is never a grant", async () => {
  for (const status of ["identity_reused", "rollout_hold", "retired"]) {
    const { client } = rpcOf(() => ({ data: [{ ...GRANT_ROW, status, wallet_id: null, amount: null }], error: null }));
    const held = valueOf(await actions().claimGrant(onboarding, () => client), status);
    assert.deepEqual(held, { status: "held" }, `${status} names no reason (no enumeration)`);
  }
  const { client: lagging } = rpcOf(() => ({ data: [{ ...GRANT_ROW, status: "unverified", amount: null }], error: null }));
  assert.equal(codeOf(await actions().claimGrant(onboarding, () => lagging)), "forbidden");
  const { client: down } = rpcOf(() => ({ data: null, error: { code: "P0001", message: "feature_disabled: signup_grant for 0e00..." } }));
  const outage = await actions().claimGrant(onboarding, () => down);
  assert.equal(codeOf(outage), "dependency_unavailable");
  assert.ok(!outage.ok && !outage.error.message.includes("signup_grant"));
  const { client: foreign } = rpcOf(() => ({ data: [{ ...GRANT_ROW, user_id: OTHER }], error: null }));
  assert.equal(codeOf(await actions().claimGrant(onboarding, () => foreign)), "internal_error");
  const { client: float } = rpcOf(() => ({ data: [{ ...GRANT_ROW, amount: 10000 }], error: null }));
  assert.equal(codeOf(await actions().claimGrant(onboarding, () => float)), "internal_error", "a number is not an exact CREDIT string");
  const { client: empty } = rpcOf(() => ({ data: [], error: null }));
  assert.equal(codeOf(await actions().claimGrant(onboarding, () => empty)), "internal_error");
  const missing = await actions().claimGrant(onboarding, () => {
    throw new Error("SUPABASE_SERVICE_ROLE_KEY is not set");
  });
  assert.equal(codeOf(missing), "dependency_unavailable");
  assert.ok(!missing.ok && !missing.error.message.includes("SERVICE_ROLE"));
});

// ------------------------------------------------------------------------------- cross-site guard

test("same-origin: a mutation needs an Origin naming this host", () => {
  const h = (entries: Record<string, string>) => new Headers(entries);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai", host: "app.callbill.ai" })), true);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai", host: "internal:3000", "x-forwarded-host": "app.callbill.ai" })), true);
  assert.equal(sameOrigin(h({ origin: "https://evil.example", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai.evil.example", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ host: "app.callbill.ai" })), false, "no Origin is not same-origin");
  assert.equal(sameOrigin(h({ origin: "null", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai" })), false, "no host to compare");
});

// ----------------------------------------------------------------------------------- operator

const operator = { userId: "0a000000-0000-4000-8000-00000000000a", isOperator: true };
const ADJUST = { action: "adjust_credit", user_id: OTHER, amount: "-25.50000000", reason: "refund of a failed batch", idempotency_key: "adj-1" };

test("operator: authority, reason and idempotency key are required; the actor is the session", () => {
  assert.equal(codeOf(operatorCommand({ userId: ME, isOperator: false }, ADJUST)), "forbidden");
  const command = valueOf(operatorCommand(operator, ADJUST), "adjust");
  assert.deepEqual(command, { ...ADJUST, actor: `operator:${operator.userId}` });
  for (const [bad, why] of [
    [{ ...ADJUST, reason: "  " }, "blank reason"],
    [{ ...ADJUST, reason: undefined }, "no reason"],
    [{ ...ADJUST, idempotency_key: undefined }, "no idempotency key"],
    [{ ...ADJUST, amount: 25.5 }, "a float amount"],
    [{ ...ADJUST, amount: "0" }, "a zero adjustment"],
    [{ ...ADJUST, amount: "1e3" }, "not an exact decimal"],
    [{ ...ADJUST, unit: "USD" }, "a unit field"],
    [{ ...ADJUST, actor: "someone-else" }, "a client actor"],
    [{ ...ADJUST, user_id: "nope" }, "a malformed target"],
    [{ ...ADJUST, action: "set_balance" }, "an arbitrary balance edit"],
    [{ action: "set_suspension", org_id: MY_ORG, suspended: "yes", reason: "r", idempotency_key: "s" }, "a non-boolean"],
  ] as const) {
    assert.equal(codeOf(operatorCommand(operator, bad)), "invalid_request", why);
  }
  valueOf(operatorCommand(operator, { action: "set_suspension", org_id: MY_ORG, suspended: true, reason: "abuse", idempotency_key: "s1" }), "suspend");
  valueOf(operatorCommand(operator, { action: "revoke_key", key_id: KEY, reason: "leaked", idempotency_key: "r1" }), "revoke");
  valueOf(operatorCommand(operator, { action: "grant_initial", user_id: OTHER, reason: "backfill", idempotency_key: "g1" }), "grant");
});

test("operator: no deployed port is an explicit unavailable state, never a silent success", async () => {
  const command = valueOf(operatorCommand(operator, ADJUST), "adjust");
  const none = await runOperatorCommand(command, null);
  assert.equal(codeOf(none), "dependency_unavailable");
  const seen: unknown[] = [];
  const port: OperatorPort = {
    async run(given) {
      seen.push(given);
      return { ok: true, value: { replayed: false } };
    },
  };
  valueOf(await runOperatorCommand(command, port), "through a port");
  assert.deepEqual(seen, [command]);
  const throwing: OperatorPort = {
    async run() {
      throw new Error("connect ECONNREFUSED 10.0.0.9:5432");
    },
  };
  const failed = await runOperatorCommand(command, throwing);
  assert.equal(codeOf(failed), "dependency_unavailable");
  assert.ok(!failed.ok && !failed.error.message.includes("10.0.0.9"));
});
