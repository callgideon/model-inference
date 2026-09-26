// node --test "tests/**/*.test.ts"
//
// C0: the real consumer context and read port (CONSOLE-TENANT, CREDIT-UNITS).
//
// The decisions under test, each with the defect it catches:
// - the account is the caller's OWN consumer wallet and its personal organization, found by owner,
//   never the first membership and never an operator/provider organization;
// - a failed read is `unavailable` (or a non-ok Result), never "onboarding", never a zero balance;
// - CREDIT stays an exact decimal string in its own unit; USD history is a separate statement;
// - pages walk ties on `created_at` exactly once; a cursor is bound to the account it was minted for;
// - result access goes through the D10 RPC and its typed refusals, and the DB's own message text
//   (which can name identifiers) never reaches a caller.
//
// The same port runs against real PostgREST in `consumer-postgrest.test.ts`.

import assert from "node:assert/strict";
import test from "node:test";
import type { PostgrestClient, QueryPlan, QueryPort, Row } from "../../lib/services/query.ts";
import { buildPlan, postgrestPort, QueryPortError } from "../../lib/services/query.ts";
import {
  authUserOutcome,
  consoleShell,
  consumerSessionFrom,
  createConsumerReads,
  resolveConsumerContext,
  type ConsumerAccount,
  type AuthUser,
  type ConsumerClient,
  type RpcClient,
} from "../../lib/services/console.ts";
import { creditBalanceOf, sidebarCredit } from "../../lib/services/credits.ts";
import type { Result } from "../../lib/contracts/types.ts";
import { createMemoryPort, type Dataset } from "./harness.ts";

/** Success, or a failure that prints the code that came back (the mutant runner reads it). */
function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}

const SECRET = "c0-test-cursor-secret-0123456789";
const ME = "c1000000-0000-4000-8000-000000000001";
const OTHER = "c1000000-0000-4000-8000-000000000002";
const MY_WALLET = "aaaaaaaa-0000-4000-8000-000000000001";
const OTHER_WALLET = "aaaaaaaa-0000-4000-8000-000000000002";
const MY_ORG = "0e000000-0000-4000-8000-000000000001";
const OTHER_ORG = "0e000000-0000-4000-8000-000000000002";
const SHARED_ORG = "0e000000-0000-4000-8000-000000000003";
const AT = "2026-09-01T12:00:00+00:00";

const verified = { id: ME, email: "me@example.com", email_confirmed_at: "2026-09-01T11:00:00Z" };
const account: ConsumerAccount = { userId: ME, email: "me@example.com", walletId: MY_WALLET, orgId: MY_ORG, suspended: false };

/** What `console_credit_wallets` hands an operator: every wallet, so the owner predicate matters. */
function world(): Dataset {
  return {
    credit_wallets: [
      { owner_user_id: OTHER, wallet_id: OTHER_WALLET, org_id: OTHER_ORG, kind: "consumer" },
      { owner_user_id: ME, wallet_id: MY_WALLET, org_id: MY_ORG, kind: "consumer" },
    ],
    orgs: [
      { org_id: SHARED_ORG, name: "Shared", suspended: false, suspension_reason: null },
      { org_id: MY_ORG, name: "Personal", suspended: false, suspension_reason: null },
      { org_id: OTHER_ORG, name: "Personal", suspended: true, suspension_reason: "x" },
    ],
    credit_ledger: [],
    keys: [],
  };
}

const failingPort: QueryPort = {
  async run() {
    throw new QueryPortError("fetch failed");
  },
};

/** A port that forgot every predicate, the tenant included: returns the whole relation. */
function predicateBlindPort(data: Dataset): QueryPort {
  const inner = createMemoryPort(data);
  return {
    run(plan: QueryPlan) {
      return inner.run({ ...plan, predicates: [], tenant: null });
    },
  };
}

type Answer = { data: unknown; error: { code?: string; message?: string } | null };

function rpcOf(handlers: Record<string, (args: Record<string, unknown>) => Answer>) {
  const calls: [string, Record<string, unknown>][] = [];
  const client: RpcClient = {
    rpc(fn, args) {
      calls.push([fn, args]);
      const handler = handlers[fn];
      return Promise.resolve(handler ? handler(args) : { data: null, error: { code: "PGRST202", message: "no such function" } });
    },
  };
  return { client, calls };
}

// ---------------------------------------------------------------------------------------- auth

test("the auth answer: a user, signed out, or unavailable - an outage is never 'signed out'", () => {
  assert.deepEqual(authUserOutcome({ data: { user: verified }, error: null }), verified);
  assert.equal(authUserOutcome({ data: { user: null }, error: null }), null);
  assert.equal(authUserOutcome({ data: { user: null }, error: { name: "AuthSessionMissingError", status: 400 } }), null);
  assert.equal(authUserOutcome({ data: { user: null }, error: { name: "AuthApiError", status: 401 } }), null);
  assert.equal(authUserOutcome({ data: { user: null }, error: { name: "AuthRetryableFetchError", status: 0 } }), "unavailable");
  assert.equal(authUserOutcome({ data: { user: null }, error: { name: "AuthApiError", status: 500 } }), "unavailable");
});

// ------------------------------------------------------------------------------------- context

test("signed out and unverified are typed states that read nothing", async () => {
  assert.deepEqual(await resolveConsumerContext(failingPort, null), { state: "signed_out" });
  const unverified = await resolveConsumerContext(failingPort, { ...verified, email_confirmed_at: null });
  assert.deepEqual(unverified, { state: "unverified", userId: ME, email: "me@example.com" });
});

test("the account is the caller's own consumer wallet and its personal org, never the first membership", async () => {
  const context = await resolveConsumerContext(createMemoryPort(world()), verified);
  assert.deepEqual(context, { state: "ready", account });
});

test("a verified individual without a wallet is onboarding, whatever other wallets the view shows", async () => {
  const data = world();
  data.credit_wallets = data.credit_wallets.filter((row) => row.owner_user_id !== ME);
  const context = await resolveConsumerContext(createMemoryPort(data), verified);
  assert.deepEqual(context, { state: "onboarding", userId: ME, email: "me@example.com" });
});

test("a port that drops the owner predicate cannot hand over another individual's wallet", async () => {
  // Exactly one row comes back - someone else's - so only the per-row owner check can refuse it.
  const data = world();
  data.credit_wallets = data.credit_wallets.filter((row) => row.owner_user_id === OTHER);
  const context = await resolveConsumerContext(predicateBlindPort(data), verified);
  assert.deepEqual(context, { state: "unavailable" });
});

test("a failed wallet read is unavailable, never onboarding (which would offer a second grant)", async () => {
  assert.deepEqual(await resolveConsumerContext(failingPort, verified), { state: "unavailable" });
});

test("a suspended personal organization is still the account, marked suspended", async () => {
  const data = world();
  data.orgs = data.orgs.map((row) => (row.org_id === MY_ORG ? { ...row, suspended: true } : row));
  const context = await resolveConsumerContext(createMemoryPort(data), verified);
  assert.deepEqual(context, { state: "ready", account: { ...account, suspended: true } });
});

// ------------------------------------------------------------------------------------- balance

const summary = (over: Record<string, unknown> = {}) => ({
  user_id: ME,
  wallet_id: MY_WALLET,
  kind: "consumer",
  unit: "CREDIT",
  ledger_total: "10000.00000000",
  reserved_total: "0.12345679",
  available: "9999.87654321",
  revision: 3,
  signup_granted_at: AT,
  ...over,
});

test("the CREDIT balance is exact, in its own unit, and recomputed rather than trusted", () => {
  const ok = creditBalanceOf(summary(), MY_WALLET);
  assert.deepEqual(ok, {
    ok: true,
    value: {
      schema_version: 2,
      wallet_id: MY_WALLET,
      kind: "consumer",
      unit: "CREDIT",
      ledger_total: "10000.00000000",
      reserved_total: "0.12345679",
      available: "9999.87654321",
    },
  });
  const huge = creditBalanceOf(
    summary({ ledger_total: "999999999999.99999999", reserved_total: "0.00000001", available: "999999999999.99999998" }),
    MY_WALLET,
  );
  assert.equal(huge.ok && huge.value.available, "999999999999.99999998");
  assert.equal(sidebarCredit(ok), "9,999.87654321 credits");
});

test("a balance that is not exactly this wallet's is not a balance (never a zero)", () => {
  const refused: [string, unknown][] = [
    ["no wallet (the summary's all-zero row)", summary({ wallet_id: null, ledger_total: "0.00000000", reserved_total: "0.00000000", available: "0.00000000" })],
    ["another wallet", summary({ wallet_id: OTHER_WALLET })],
    ["dollars", summary({ unit: "USD" })],
    ["a float", summary({ ledger_total: 10000 })],
    ["drift between the stored and derived available", summary({ available: "9999.87654322" })],
    ["a missing column", { wallet_id: MY_WALLET, unit: "CREDIT", kind: "consumer" }],
    ["not a row", null],
  ];
  for (const [why, row] of refused) {
    const result = creditBalanceOf(row, MY_WALLET);
    assert.equal(result.ok, false, why);
    assert.equal(sidebarCredit(result), null, `${why}: the sidebar shows fixed copy, not an amount`);
  }
});

test("reads.balance: a failed or empty summary is unavailable with fixed text, never a zero", async () => {
  const down = rpcOf({ console_wallet_summary: () => ({ data: null, error: { code: "", message: "TypeError: fetch failed at 10.0.0.1" } }) });
  const reads = createConsumerReads({ pg: failingPort, rpc: down.client, cursorSecret: SECRET }, account);
  const result = await reads.balance();
  assert.equal(result.ok, false);
  assert.equal(!result.ok && result.error.code, "dependency_unavailable");
  assert.ok(!result.ok && !result.error.message.includes("10.0.0.1"), "the transport's text stays out of the answer");
  assert.deepEqual(down.calls, [["console_wallet_summary", { p_user: ME }]], "the summary is asked for this user only");

  const empty = rpcOf({ console_wallet_summary: () => ({ data: [], error: null }) });
  const none = await createConsumerReads({ pg: failingPort, rpc: empty.client, cursorSecret: SECRET }, account).balance();
  assert.equal(none.ok, false);

  const good = rpcOf({ console_wallet_summary: () => ({ data: [summary()], error: null }) });
  const fine = await createConsumerReads({ pg: failingPort, rpc: good.client, cursorSecret: SECRET }, account).balance();
  assert.equal(fine.ok && fine.value.available, "9999.87654321");
});

test("legacy USD is its own statement: none is null, history is exact USD, never CREDIT", async () => {
  const statement = (over: Record<string, unknown>) => ({
    org_id: MY_ORG,
    accounting_regime: "legacy_usd",
    unit: "USD",
    balance: "0.00000000",
    entry_count: 0,
    as_of: AT,
    rollout_hold: false,
    ...over,
  });
  const read = async (row: unknown) => {
    const rpc = rpcOf({ console_legacy_usd_statement: () => ({ data: [row], error: null }) });
    const result = await createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account).legacyUsd();
    assert.deepEqual(rpc.calls, [["console_legacy_usd_statement", { p_org: MY_ORG }]]);
    return result;
  };
  assert.deepEqual(await read(statement({})), { ok: true, value: null });
  assert.deepEqual(await read(statement({ balance: "12.34567891", entry_count: 2, rollout_hold: true })), {
    ok: true,
    value: {
      schema_version: 2,
      org_id: MY_ORG,
      balance: "12.34567891",
      entry_count: 2,
      as_of: "2026-09-01T12:00:00.000000Z",
      rollout_hold: true,
    },
  });
  assert.equal((await read(statement({ unit: "CREDIT", entry_count: 1 }))).ok, false, "a statement in CREDIT is refused");
  assert.equal((await read(statement({ org_id: OTHER_ORG, entry_count: 1 }))).ok, false, "another org's statement is refused");
});

// -------------------------------------------------------------------------------------- ledger

function ledgerRow(n: number, over: Record<string, unknown> = {}): Row {
  const entry = `eeeeeeee-0000-4000-8000-${String(n).padStart(12, "0")}`;
  return {
    entry_id: entry,
    created_at: AT,
    kind: n === 0 ? "signup_grant" : "inference_debit",
    amount: n === 0 ? "10000.00000000" : `-0.${String(n).padStart(8, "0")}`,
    unit: "CREDIT",
    request_id: n === 0 ? null : `5c000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    reason: "",
    cursor: `2026-09-01 12:00:00+00|${entry}`,
    ...over,
  };
}

const ledgerRpc = (rows: Row[]) => rpcOf({ consumer_credit_ledger: () => ({ data: rows, error: null }) });
const ledgerReads = (rpc: RpcClient) => createConsumerReads({ pg: failingPort, rpc, cursorSecret: SECRET }, account);

test("the CREDIT ledger pages through consumer_credit_ledger: no tenant argument, the DB's cursor, exact CREDIT", async () => {
  const rpc = ledgerRpc([4, 3, 2].map((n) => ledgerRow(n)));
  const page = valueOf(await ledgerReads(rpc.client).ledger({ limit: 2 }), "the first page");
  // C0 WR-5 (0024): the JWT subject's own wallet, one index range stopped by its LIMIT - O(limit).
  assert.deepEqual(rpc.calls, [["consumer_credit_ledger", { p_after: null, p_limit: 3 }]], "no wallet argument: the DB takes it from the JWT");
  assert.deepEqual(page.items[0], {
    entry_id: ledgerRow(4).entry_id,
    created_at: "2026-09-01T12:00:00.000000Z",
    kind: "inference_debit",
    amount: "-0.00000004",
    request_id: "5c000000-0000-4000-8000-000000000004",
    reason: "",
  });
  assert.equal(page.items.length, 2, "the look-ahead row is a probe, not an item");
  assert.equal(page.next_cursor, ledgerRow(3).cursor, "the next page resumes after the last row shown");
  const second = ledgerRpc([ledgerRow(2)]);
  const last = valueOf(await ledgerReads(second.client).ledger({ limit: 2, cursor: page.next_cursor }), "the last page");
  assert.deepEqual(second.calls[0][1], { p_after: ledgerRow(3).cursor, p_limit: 3 }, "the cursor is passed through, opaque");
  assert.equal(last.next_cursor, null);
});

test("the ledger's look-ahead is clamped at the read's 100 cap; a limit over 100 is refused before any call", async () => {
  const full = ledgerRpc(Array.from({ length: 100 }, (_, n) => ledgerRow(n + 1)));
  const top = valueOf(await ledgerReads(full.client).ledger({ limit: 100 }), "limit 100 is not refused by the database");
  assert.deepEqual(full.calls[0][1], { p_after: null, p_limit: 100 }, "limit + 1 = 101 would be refused 400 invalid_request");
  assert.equal(top.next_cursor, ledgerRow(100).cursor, "a full capped page is never read as the last one");
  const none = ledgerRpc([]);
  const big = await ledgerReads(none.client).ledger({ limit: 101 });
  assert.equal(!big.ok && big.error.code, "invalid_request");
  assert.deepEqual(none.calls, [], "refused before the call");
});

test("a ledger row that is not CREDIT, or of an unknown kind, fails the page instead of rendering", async () => {
  for (const over of [{ unit: "USD" }, { kind: "transfer" }, { amount: 5 }]) {
    const page = await ledgerReads(ledgerRpc([ledgerRow(1, over)]).client).ledger({});
    assert.equal(page.ok, false, JSON.stringify(over));
  }
});

test("a port failure is dependency_unavailable (keys, the ledger RPC); a stale ledger cursor is invalid_cursor", async () => {
  const reads = createConsumerReads({ pg: failingPort, rpc: rpcOf({}).client, cursorSecret: SECRET }, account);
  const keys = await reads.keys();
  assert.equal(!keys.ok && keys.error.code, "dependency_unavailable");
  const down = await reads.ledger({});
  assert.equal(!down.ok && down.error.code, "dependency_unavailable");
  const stale = rpcOf({ consumer_credit_ledger: () => ({ data: null, error: { code: "P0001", message: "invalid_cursor: not a cursor this read issued" } }) });
  const refused = await ledgerReads(stale.client).ledger({ cursor: "x|y" });
  assert.equal(!refused.ok && refused.error.code, "invalid_cursor");
});

// ------------------------------------------------------------------------------------ requests

function job(n: number, over: Record<string, unknown> = {}): Row {
  return {
    request_id: `5c000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    job_handle: `job_${n}`,
    created_at: AT,
    requested_model: "nemostation/marlin-2b",
    model_revision: "nemostation/marlin-2b@2026-09-01",
    execution_mode: "async",
    state: "succeeded",
    outcome_cause: "completed",
    accounting_regime: "credit",
    settlement_state: "settled",
    usage_certainty: "authoritative",
    prompt_tokens: 1200,
    completion_tokens: 340,
    unit: "CREDIT",
    hold: "0.50000000",
    hold_state: "settled",
    charged: "0.12345678",
    result_available: true,
    result_expires_at: "2026-09-02T12:00:00+00:00",
    settled_at: AT,
    cursor: `2026-09-01 12:00:00+00|5c000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    ...over,
  };
}

test("requests: exact charge in the row's own unit; unsettled is null, never a zero", async () => {
  const rows = [
    job(1),
    job(2, { accounting_regime: "legacy_usd", unit: "USD", charged: "0.00012000", hold: null, hold_state: null }),
    job(3, { state: "running", settlement_state: null, charged: null, settled_at: null, result_available: false, result_expires_at: null, prompt_tokens: null, completion_tokens: null, usage_certainty: null }),
  ];
  const rpc = rpcOf({ consumer_jobs: () => ({ data: rows, error: null }) });
  const reads = createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account);
  const page = { value: valueOf(await reads.requests({ limit: 5 }), "the request page") };
  assert.deepEqual(rpc.calls, [["consumer_jobs", { p_after: null, p_limit: 6 }]], "no tenant argument: the DB takes it from the JWT");
  const [credit, usd, running] = page.value.items;
  assert.deepEqual(
    [credit.unit, credit.charged, credit.hold, credit.result, credit.usage],
    ["CREDIT", "0.12345678", "0.50000000", "available", { prompt_tokens: 1200, completion_tokens: 340 }],
  );
  assert.deepEqual([usd.unit, usd.charged, usd.hold], ["USD", "0.00012000", null]);
  assert.deepEqual([running.charged, running.result, running.usage], [null, "pending", null]);
  assert.equal(page.value.next_cursor, null);
  assert.equal(credit.created_at, "2026-09-01T12:00:00.000000Z");
  assert.ok(!("job_handle" in credit) && !("cursor" in credit), "only the columns a page needs");
});

test("requests: the result state comes from the DB's persisted expiry, in contract order", async () => {
  const cases: [Record<string, unknown>, string][] = [
    [{}, "available"],
    [{ result_available: false }, "expired"],
    [{ result_available: false, result_expires_at: null }, "unavailable"],
    [{ state: "failed", outcome_cause: "engine_error", result_available: false, result_expires_at: null }, "no_result"],
    [{ settlement_state: "held_unknown", charged: null, result_available: false }, "held_unknown"],
    [{ settled_at: null, settlement_state: null, charged: null, result_available: false, result_expires_at: null }, "pending"],
  ];
  for (const [over, want] of cases) {
    const rpc = rpcOf({ consumer_jobs: () => ({ data: [job(1, over)], error: null }) });
    const page = await createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account).requests({});
    assert.equal(page.ok && page.value.items[0].result, want, JSON.stringify(over));
  }
});

test("requests: a row whose unit contradicts its regime, or half a usage report, fails the page", async () => {
  for (const over of [
    { unit: "USD" },
    { accounting_regime: "legacy_usd" },
    { accounting_regime: "provider_usd", unit: "PROVIDER_USD" },
    { completion_tokens: null },
    { charged: 0.1 },
  ]) {
    const rpc = rpcOf({ consumer_jobs: () => ({ data: [job(1, over)], error: null }) });
    const page = await createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account).requests({});
    assert.equal(page.ok, false, JSON.stringify(over));
  }
});

test("requests: pages resume from the DB's cursor, including at the 100-row cap", async () => {
  const three = rpcOf({ consumer_jobs: () => ({ data: [job(1), job(2), job(3)], error: null }) });
  const page = await createConsumerReads({ pg: failingPort, rpc: three.client, cursorSecret: SECRET }, account).requests({ limit: 2, cursor: "c" });
  assert.deepEqual(three.calls, [["consumer_jobs", { p_after: "c", p_limit: 3 }]]);
  assert.equal(page.ok && page.value.items.length, 2);
  assert.equal(page.ok && page.value.next_cursor, job(2).cursor, "the next page starts after the last row shown");

  const full = Array.from({ length: 100 }, (_, n) => job(n + 1));
  const capped = rpcOf({ consumer_jobs: () => ({ data: full, error: null }) });
  const top = await createConsumerReads({ pg: failingPort, rpc: capped.client, cursorSecret: SECRET }, account).requests({ limit: 100 });
  assert.deepEqual(capped.calls[0][1], { p_after: null, p_limit: 100 }, "the RPC caps p_limit at 100");
  assert.equal(top.ok && top.value.next_cursor, job(100).cursor, "a full capped page is never read as the last one");

  const bad = rpcOf({ consumer_jobs: () => ({ data: null, error: { code: "P0001", message: "invalid_cursor: not a cursor this read issued" } }) });
  const refused = await createConsumerReads({ pg: failingPort, rpc: bad.client, cursorSecret: SECRET }, account).requests({ cursor: "x|y" });
  assert.equal(!refused.ok && refused.error.code, "invalid_cursor");
});

test("request detail: a malformed id is not_found without a query; an unknown one is not_found", async () => {
  const rpc = rpcOf({ consumer_jobs: () => ({ data: [], error: null }) });
  const reads = createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account);
  const malformed = await reads.request("1; drop table");
  assert.equal(!malformed.ok && malformed.error.code, "not_found");
  assert.equal(rpc.calls.length, 0);
  const id = job(7).request_id as string;
  const unknown = await reads.request(id);
  assert.equal(!unknown.ok && unknown.error.code, "not_found");
  assert.deepEqual(rpc.calls, [["consumer_jobs", { p_after: null, p_limit: 1, p_request_id: id }]]);
});

test("result: the owned body through the D10 RPC; typed refusals keep a fixed message", async () => {
  const id = job(1).request_id as string;
  const read = async (answer: Answer) => {
    const rpc = rpcOf({ consumer_job_result: () => answer });
    const result = await createConsumerReads({ pg: failingPort, rpc: rpc.client, cursorSecret: SECRET }, account).result(id);
    assert.deepEqual(rpc.calls, [["consumer_job_result", { p_request_id: id }]]);
    return result;
  };
  assert.deepEqual(await read({ data: "the answer", error: null }), { ok: true, value: "the answer" });
  for (const code of ["not_found", "result_pending", "result_expired"]) {
    const refused = await read({ data: null, error: { code: "P0001", message: `${code}: no result infrx-result:${id}` } });
    assert.equal(!refused.ok && refused.error.code, code);
    assert.ok(!refused.ok && !refused.error.message.includes(id), "the DB text is not echoed");
  }
  const down = await read({ data: null, error: { code: "", message: "fetch failed" } });
  assert.equal(!down.ok && down.error.code, "dependency_unavailable");
  const odd = await read({ data: { body: "x" }, error: null });
  assert.equal(odd.ok, false, "a result is text or nothing");
});

test("keys: this personal organization's key metadata only, never a hash", async () => {
  const data = world();
  const key = (id: string, org: string) => ({
    org_id: org,
    id,
    name: "k",
    prefix: "sk-infrx-abc",
    created_at: AT,
    last_used_at: null,
    revoked_at: null,
    trace_mode: null,
    key_hash: "never",
  });
  data.keys = [key("k0000000-0000-4000-8000-000000000001", MY_ORG), key("k0000000-0000-4000-8000-000000000002", SHARED_ORG)];
  const reads = createConsumerReads({ pg: createMemoryPort(data), rpc: rpcOf({}).client, cursorSecret: SECRET }, account);
  const keys = valueOf(await reads.keys(), "the key list");
  assert.deepEqual(keys.map((k) => k.id), ["k0000000-0000-4000-8000-000000000001"]);
  assert.ok(!("key_hash" in keys[0]), "no hash column reaches a key summary");
  const blind = createConsumerReads({ pg: predicateBlindPort(data), rpc: rpcOf({}).client, cursorSecret: SECRET }, account);
  assert.equal((await blind.keys()).ok, false, "a key of another organization fails the list");
});

// ---------------------------------------------------------------------------- PostgREST executor

type Call = [string, ...unknown[]];

function recordingClient(answer: Answer) {
  const calls: Call[] = [];
  const builder: Record<string, unknown> = {};
  for (const method of ["select", "eq", "neq", "gte", "lte", "gt", "lt", "not", "or", "order", "limit"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  builder.then = (resolve: (value: Answer) => unknown, reject: (reason: unknown) => unknown) =>
    Promise.resolve(answer).then(resolve, reject);
  const client = {
    from(relation: string) {
      calls.push(["from", relation]);
      return builder;
    },
    rpc() {
      throw new Error("not used");
    },
  };
  return { client: client as unknown as PostgrestClient, calls };
}

test("the PostgREST executor sends the plan: relation, named columns, filters, keyset, tenant last", async () => {
  const { client, calls } = recordingClient({ data: [], error: null });
  const port = postgrestPort(client);
  await port.run({
    name: "credit_ledger_page",
    predicates: [],
    keyset: { at: "2026-09-01T12:00:00.000000Z", id: "eeeeeeee-0000-4000-8000-000000000004" },
    limit: 26,
    tenant: { column: "l.wallet_id", value: MY_WALLET },
  });
  assert.deepEqual(calls, [
    ["from", "console_credit_ledger"],
    ["select", "wallet_id,entry_id,created_at,kind,amount,unit,request_id,reason"],
    [
      "or",
      // supabase-js adds the surrounding `or=( … )` (found by the real PostgREST run).
      'created_at.lt."2026-09-01T12:00:00.000000Z",and(created_at.eq."2026-09-01T12:00:00.000000Z",entry_id.lt."eeeeeeee-0000-4000-8000-000000000004")',
    ],
    ["eq", "wallet_id", MY_WALLET],
    ["order", "created_at", { ascending: false }],
    ["order", "entry_id", { ascending: false }],
    ["limit", 26],
  ]);

  const second = recordingClient({ data: [], error: null });
  // Built by the registry, so the query's own `kind = consumer` constant is part of what is sent.
  await postgrestPort(second.client).run(buildPlan("consumer_wallet", { orgId: ME, limit: 2 }));
  assert.deepEqual(second.calls, [
    ["from", "console_credit_wallets"],
    ["select", "owner_user_id,wallet_id,org_id"],
    ["eq", "kind", "consumer"],
    ["eq", "owner_user_id", ME],
    ["limit", 2],
  ]);

  const aliased = recordingClient({ data: [], error: null });
  await postgrestPort(aliased.client).run({ name: "org_status", predicates: [], keyset: null, limit: 1, tenant: { column: "o.id", value: MY_ORG } });
  assert.deepEqual(aliased.calls.slice(0, 3), [
    ["from", "organizations"],
    ["select", "org_id:id,name,suspended,suspension_reason"],
    ["eq", "id", MY_ORG],
  ]);
});

test("the PostgREST executor refuses what it cannot send, and turns an error into a port failure", async () => {
  const { client } = recordingClient({ data: [], error: null });
  await assert.rejects(
    postgrestPort(client).run({ name: "usage_summary", predicates: [], keyset: null, limit: null, tenant: { column: "u.org_id", value: MY_ORG } }),
    /no PostgREST form/,
  );
  await assert.rejects(
    postgrestPort(client).run({
      name: "credit_ledger_page",
      predicates: [],
      keyset: { at: "x", id: 'a"),or(' },
      limit: 2,
      tenant: { column: "l.wallet_id", value: MY_WALLET },
    }),
    /keyset/,
  );
  const failing = recordingClient({ data: null, error: { code: "42501", message: "permission denied for view" } });
  await assert.rejects(
    postgrestPort(failing.client).run({ name: "consumer_wallet", predicates: [], keyset: null, limit: 1, tenant: { column: "w.owner_user_id", value: ME } }),
    (error: unknown) => error instanceof QueryPortError && !String((error as Error).message).includes("permission denied"),
  );
});

// ------------------------------------------------------------------ the request's session and shell
//
// `consumerSessionFrom` is `consumerSession()` minus React's `cache` and the Next cookie client
// (server.ts is a one-line wrapper over it); `consoleShell` is what `app/(console)/layout.tsx` does
// with the result (WR-1). Both are pure, so the layout's decisions are tested here.

const SHIPPED = { verifyEmail: "/verify-email", onboarding: "/onboarding" } as const;
const NOT_SHIPPED = { verifyEmail: null, onboarding: null } as const;

/** A Supabase server client: GoTrue's answer plus what PostgREST returns per relation. */
function supabaseAs(auth: { data: { user: AuthUser | null } | null; error: { name?: string; status?: number } | null }, tables: Record<string, Row[]> = {}): ConsumerClient {
  return {
    auth: { getUser: () => Promise.resolve(auth) },
    from: (relation: string) => recordingClient({ data: tables[relation] ?? [], error: null }).client.from(relation),
    rpc: () => Promise.resolve({ data: null, error: { code: "PGRST202", message: "not used" } }),
  } as unknown as ConsumerClient;
}

const signedIn = { data: { user: verified }, error: null };
const myWallet = { console_credit_wallets: [{ owner_user_id: ME, wallet_id: MY_WALLET, org_id: MY_ORG }], organizations: [{ org_id: MY_ORG, name: "Personal", suspended: false, suspension_reason: null }] };
const secret = () => SECRET;
const noSecret = () => {
  throw new Error("CONSOLE_CURSOR_SECRET must be set");
};

test("consumerSession: an auth outage is unavailable, never signed out", async () => {
  const outage = supabaseAs({ data: { user: null }, error: { name: "AuthRetryableFetchError", status: 0 } }, myWallet);
  assert.deepEqual(await consumerSessionFrom(async () => outage, secret), { context: { state: "unavailable" }, reads: null });
  const gotrue500 = supabaseAs({ data: { user: null }, error: { name: "AuthApiError", status: 500 } }, myWallet);
  assert.deepEqual(await consumerSessionFrom(async () => gotrue500, secret), { context: { state: "unavailable" }, reads: null });
});

test("consumerSession: a configuration or client failure is unavailable, never onboarding or signed out", async () => {
  const unavailable = { context: { state: "unavailable" }, reads: null };
  // No wallet, so a failure that leaked through as the resolved state would read as onboarding.
  const ungranted = supabaseAs(signedIn);
  assert.deepEqual(await consumerSessionFrom(async () => ungranted, noSecret), unavailable, "missing cursor secret");
  assert.deepEqual(await consumerSessionFrom(async () => supabaseAs(signedIn, myWallet), noSecret), unavailable, "missing secret, ready account");
  const noEnv = async (): Promise<ConsumerClient> => {
    throw new Error("Your project's URL and Key are required to create a Supabase client!");
  };
  assert.deepEqual(await consumerSessionFrom(noEnv, secret), unavailable, "createClient threw");
  const throwingAuth = { ...ungranted, auth: { getUser: () => Promise.reject(new TypeError("fetch failed")) } } as unknown as ConsumerClient;
  assert.deepEqual(await consumerSessionFrom(async () => throwingAuth, secret), unavailable, "getUser threw");
});

test("consumerSession: signed out, unverified and onboarding carry no reads; a ready account does", async () => {
  const none = await consumerSessionFrom(async () => supabaseAs({ data: { user: null }, error: { name: "AuthSessionMissingError", status: 400 } }), secret);
  assert.deepEqual(none, { context: { state: "signed_out" }, reads: null });
  const unverified = await consumerSessionFrom(async () => supabaseAs({ data: { user: { ...verified, email_confirmed_at: null } }, error: null }, myWallet), secret);
  assert.deepEqual(unverified.context, { state: "unverified", userId: ME, email: "me@example.com" });
  // `ok(x === null)`, not `equal`: a failing diff over an object of functions does not serialise, and
  // the mutant runner then cannot tell an assertion from a crash.
  assert.ok(unverified.reads === null, "an unverified user gets no reads");
  const onboarding = await consumerSessionFrom(async () => supabaseAs(signedIn), secret);
  assert.deepEqual(onboarding.context, { state: "onboarding", userId: ME, email: "me@example.com" });
  assert.ok(onboarding.reads === null, "an individual without a wallet gets no reads");
  const ready = await consumerSessionFrom(async () => supabaseAs(signedIn, myWallet), secret);
  assert.deepEqual(ready.context, { state: "ready", account });
  assert.ok(ready.reads !== null, "a ready account carries its reads");
  const balance = await ready.reads!.balance();
  assert.equal(balance.ok ? "ok" : balance.error.code, "dependency_unavailable", "the reads run on the same client");
});

test("the console shell: an operator without a consumer wallet reaches /admin, not /onboarding", async () => {
  // The operator's view shows every wallet, none of them theirs: the owner filter leaves nothing.
  const operator = await consumerSessionFrom(async () => supabaseAs(signedIn), secret);
  assert.equal(operator.context.state, "onboarding");
  assert.deepEqual(consoleShell(operator, true, SHIPPED), { kind: "render", email: "me@example.com", reads: null }, "operator: the shell renders the page asked for");
  assert.deepEqual(consoleShell(operator, true, NOT_SHIPPED), { kind: "render", email: "me@example.com", reads: null });
  // An individual (a provider developer included) is sent to finish onboarding - once that route exists.
  assert.deepEqual(consoleShell(operator, false, SHIPPED), { kind: "redirect", to: "/onboarding" });
  assert.deepEqual(consoleShell(operator, false, NOT_SHIPPED), { kind: "panel", state: "onboarding" }, "never a redirect to a missing route");
});

test("the console shell: sign-in, verification, readiness and outages", async () => {
  const signedOut = { context: { state: "signed_out" as const }, reads: null };
  assert.deepEqual(consoleShell(signedOut, false, SHIPPED), { kind: "redirect", to: "/login" });
  const unverified = { context: { state: "unverified" as const, userId: ME, email: "me@example.com" }, reads: null };
  assert.deepEqual(consoleShell(unverified, false, SHIPPED), { kind: "redirect", to: "/verify-email" });
  assert.deepEqual(consoleShell(unverified, true, SHIPPED), { kind: "redirect", to: "/verify-email" }, "an operator verifies too");
  assert.deepEqual(consoleShell(unverified, false, NOT_SHIPPED), { kind: "panel", state: "unverified" });
  const unavailable = { context: { state: "unavailable" as const }, reads: null };
  assert.deepEqual(consoleShell(unavailable, false, SHIPPED), { kind: "panel", state: "unavailable" });
  assert.deepEqual(consoleShell(unavailable, true, SHIPPED), { kind: "panel", state: "unavailable" }, "an outage is not onboarding for an operator either");
  const ready = await consumerSessionFrom(async () => supabaseAs(signedIn, myWallet), secret);
  const shell = consoleShell(ready, false, SHIPPED);
  assert.deepEqual([shell.kind, "email" in shell && shell.email], ["render", "me@example.com"]);
  assert.ok("reads" in shell && shell.reads === ready.reads, "the page gets the account's reads");
  assert.deepEqual(consoleShell({ context: ready.context, reads: null }, false, SHIPPED), { kind: "panel", state: "unavailable" }, "ready without reads");
});
