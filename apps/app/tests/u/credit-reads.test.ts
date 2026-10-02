// node --test "tests/**/*.test.ts"
//
// U1R over infrx-api (AP-09 09a): the consumer CREDIT read adapter (`app/(console)/billing/credit-reads.ts`).
// The client is the generated one over a recording `fetch`, so each case pins what reaches the API
// (which route, which query) and what an API answer becomes. `lib/api/fake.ts` serves the same
// documents to the pages' preview; tests/ap02 proves the API's documents on real PostgreSQL.
import assert from "node:assert/strict";
import test from "node:test";

import { answer, envelope, recordingApi, type Sent } from "../../lib/api/fake.ts";
import { apiCreditReads, KEYS_BOUND } from "../../app/(console)/billing/credit-reads.ts";

const WALLET = "a1000000-0000-4000-8000-00000000000a";
const C = (amount: string) => ({ amount, unit: "CREDIT" });
const CREDITS = {
  wallet_id: WALLET,
  ledger_total: C("9987.65432100"),
  reserved_total: C("12.00000000"),
  available: C("9975.65432100"),
  spent: C("12.34567900"),
  signup_granted_at: "2026-09-20T12:00:00.123456+00:00",
};

function jobDoc(over: Record<string, unknown> = {}) {
  return {
    request_id: "b1000000-0000-4000-8000-000000000001",
    created_at: "2026-09-20T12:00:00+00:00",
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
    hold: C("5.00000000"),
    hold_state: "settled",
    charged: C("1.23456789"),
    result_access: "available",
    result_expires_at: "2026-09-20T12:10:00+00:00",
    ...over,
  };
}

/** Answers each request from `script` (by path, in order); records every request. */
function scripted(script: Record<string, (Response | Error)[]>) {
  return recordingApi((sent: Sent) => {
    const queue = script[sent.path.split("?")[0]];
    if (queue === undefined || queue.length === 0) throw new Error(`unscripted call to ${sent.path}`);
    const next = queue.shift()!;
    if (next instanceof Error) throw next;
    return next;
  });
}

const T = {
  wallet: "U1R-R01 the wallet is the caller's own, from GET /console/v1/credits, read exactly; none is an explicit state",
  ledgerScope: "U1R-R02 the ledger is the caller's own GET /console/v1/credit-ledger page on the API's cursor, never a caller-named wallet",
  jobs: "U1R-R03 jobs page through GET /console/v1/requests on the API's own cursor",
  filters: "U1R-R08 the model, key and window filters reach the API each as its own query parameter; unset ones are not sent",
  keys: "U1R-R09 the key filter's options are the caller's keys, bounded",
  unavailable: "U1R-R04 an error, a transport failure or an inexact document is an explicit failure, never a zero",
  units: "U1R-R05 a request's unit follows its regime, and a CREDIT request with a USD amount is refused",
  spent: "U1R-R06 spent is the API's own figure; when the API cannot say, it is unknown (never derived here)",
  legacy: "U1R-R07 the legacy USD statement is the session's own, in USD",
  cap: "U1R-R10 a limit outside 1..100 is refused before the call; a page passes the API's cursor back untouched",
};

test(T.wallet, async () => {
  const { api, sent } = scripted({ "/console/v1/credits": [answer(200, CREDITS), answer(200, { ...CREDITS, wallet_id: null })] });
  const reads = apiCreditReads(api);
  assert.deepEqual(await reads.wallet(), {
    ok: true,
    value: {
      walletId: WALLET,
      ledgerTotal: "9987.65432100",
      reservedTotal: "12.00000000",
      available: "9975.65432100",
      spent: "12.34567900",
      signupGrantedAt: "2026-09-20T12:00:00.123456Z",
    },
  });
  assert.deepEqual(sent.map((s) => [s.method, s.path]), [["GET", "/console/v1/credits"]], "no owner or wallet argument: the session is the scope");
  assert.deepEqual(await reads.wallet(), { ok: true, value: null });
});

test(T.ledgerScope, async () => {
  const entry = (n: number) => ({
    entry_id: `e1000000-0000-4000-8000-00000000000${n}`,
    created_at: "2026-09-20T12:00:00+00:00",
    kind: "inference_debit",
    amount: C("-1.00000000"),
    request_id: `b1000000-0000-4000-8000-00000000000${n}`,
    reason: "inference",
  });
  const { api, sent } = scripted({
    "/console/v1/credit-ledger": [answer(200, { data: [entry(1), entry(2)], next_cursor: "opaque-2" }), answer(200, { data: [entry(3)], next_cursor: null })],
  });
  const reads = apiCreditReads(api);
  const first = await reads.ledger({ limit: 2, cursor: null });
  assert.ok(first.ok, "the first ledger page");
  assert.equal(first.value.items.length, 2);
  assert.equal(first.value.items[0].amount, "-1.00000000");
  assert.equal(first.value.items[0].requestId, "b1000000-0000-4000-8000-000000000001");
  assert.equal(first.value.next_cursor, "opaque-2", "the API's cursor, as given");
  const second = await reads.ledger({ limit: 2, cursor: first.value.next_cursor });
  assert.ok(second.ok, "the second ledger page");
  assert.deepEqual(sent.map((s) => s.path), ["/console/v1/credit-ledger?limit=2", "/console/v1/credit-ledger?limit=2&cursor=opaque-2"]);
  assert.equal(second.value.next_cursor, null);
  const usd = await apiCreditReads(scripted({ "/console/v1/credit-ledger": [answer(200, { data: [{ ...entry(1), amount: { amount: "-1.00000000", unit: "USD" } }] })] }).api).ledger({ limit: 2, cursor: null });
  assert.equal(usd.ok ? null : usd.error.code, "internal_error", "a CREDIT ledger entry in another unit is refused, never relabelled");
  const stale = await apiCreditReads(scripted({ "/console/v1/credit-ledger": [answer(400, envelope("invalid_cursor"))] }).api).ledger({ limit: 2, cursor: "x),or(wallet_id.neq.0" });
  assert.equal(stale.ok ? null : stale.error.code, "invalid_cursor");
});

test(T.jobs, async () => {
  const docs = [1, 2].map((n) => jobDoc({ request_id: `b1000000-0000-4000-8000-00000000000${n}` }));
  const { api, sent } = scripted({ "/console/v1/requests": [answer(200, { data: docs, next_cursor: "c2" }), answer(200, { data: docs.slice(0, 1), next_cursor: null })] });
  const reads = apiCreditReads(api);
  const page = await reads.jobs({ limit: 2, cursor: null });
  assert.ok(page.ok, "the requests page");
  assert.equal(page.value.items.length, 2);
  assert.equal(page.value.next_cursor, "c2");
  const job = page.value.items[0];
  assert.deepEqual([job.unit, job.charged, job.hold, job.createdAt, job.resultAvailable], ["CREDIT", "1.23456789", "5.00000000", "2026-09-20T12:00:00.000000Z", true]);
  const last = await reads.jobs({ limit: 2, cursor: "c2" });
  assert.ok(last.ok, "the last requests page");
  assert.equal(sent[1].path, "/console/v1/requests?limit=2&cursor=c2");
  assert.equal(last.value.next_cursor, null);
});

test(T.filters, async () => {
  const empty = () => answer(200, { data: [], next_cursor: null });
  const { api, sent } = scripted({ "/console/v1/requests": [empty(), empty(), empty()] });
  const reads = apiCreditReads(api);
  const KEY = "c7000000-0000-4000-8000-0000000000c1";
  await reads.jobs({ limit: 2, cursor: "c1", model: "m@1", keyId: KEY, from: "2026-09-19T12:00:00.000000Z", to: "2026-09-20T12:00:00.000000Z" });
  const query = Object.fromEntries(new URL(sent[0].path, "http://x").searchParams);
  assert.deepEqual(query, { limit: "2", cursor: "c1", model: "m@1", key_id: KEY, from: "2026-09-19T12:00:00.000000Z", to: "2026-09-20T12:00:00.000000Z" });
  await reads.jobs({ limit: 2, cursor: null, model: null, keyId: null, from: null, to: null });
  await reads.jobs({ limit: 2, cursor: null });
  assert.deepEqual([sent[1].path, sent[2].path], ["/console/v1/requests?limit=2", "/console/v1/requests?limit=2"], "an empty filter is the unfiltered call exactly");
});

test(T.keys, async () => {
  const key = { id: "c7000000-0000-4000-8000-0000000000f1", name: "laptop", prefix: "sk-infrx-abcd", created_at: "2026-09-20T08:00:00Z", last_used_at: null, revoked_at: null };
  const { api, sent } = scripted({ "/console/v1/keys": [answer(200, { data: [key] }), answer(503, envelope("dependency_unavailable"))] });
  const reads = apiCreditReads(api);
  assert.deepEqual(await reads.keys(), { ok: true, value: [{ id: key.id, name: "laptop", prefix: "sk-infrx-abcd" }] });
  assert.equal(sent[0].path, `/console/v1/keys?limit=${KEYS_BOUND}`);
  const down = await reads.keys();
  assert.equal(down.ok ? "ok" : down.error.code, "dependency_unavailable", "no options is never an empty key list");
});

test(T.unavailable, async () => {
  const { api } = scripted({
    "/console/v1/credits": [
      answer(503, envelope("dependency_unavailable")),
      new Error("fetch failed"),
      answer(200, { ...CREDITS, ledger_total: C("9987.654321001") }),
      answer(200, { ...CREDITS, available: C("1e3") }),
      new Response("<html>", { status: 502 }),
    ],
    "/console/v1/requests": [answer(400, envelope("invalid_cursor")), answer(403, envelope("forbidden")), answer(401, envelope("invalid_api_key"))],
  });
  const reads = apiCreditReads(api);
  const codes = [];
  for (let n = 0; n < 5; n += 1) {
    const got = await reads.wallet();
    assert.equal(got.ok, false, `wallet read ${n} produced a value`);
    if (!got.ok) codes.push(got.error.code);
  }
  assert.deepEqual(codes, ["dependency_unavailable", "dependency_unavailable", "internal_error", "internal_error", "internal_error"]);
  const stale = await reads.jobs({ limit: 2, cursor: "c9" });
  assert.equal(stale.ok ? null : stale.error.code, "invalid_cursor");
  const denied = await reads.jobs({ limit: 2, cursor: null });
  assert.equal(denied.ok ? null : denied.error.code, "forbidden");
  const ended = await reads.jobs({ limit: 2, cursor: null });
  assert.equal(ended.ok ? null : ended.error.code, "forbidden", "a session the API refuses is forbidden, never an outage");
});

test(T.units, async () => {
  const page = (doc: object) => answer(200, { data: [doc], next_cursor: null });
  const { api } = scripted({
    "/console/v1/requests": [
      page(jobDoc({ accounting_regime: "legacy_usd", charged: { amount: "0.00019660", unit: "USD" }, hold: { amount: "0.00500000", unit: "USD" } })),
      page(jobDoc({ charged: { amount: "1.00000000", unit: "USD" } })),
      page(jobDoc({ hold_state: "frozen" })),
      page(jobDoc({ settlement_state: null, hold_state: "held", charged: null, prompt_tokens: null, completion_tokens: null, usage_certainty: null, result_access: "pending" })),
    ],
  });
  const reads = apiCreditReads(api);
  const usd = await reads.jobs({ limit: 5, cursor: null });
  assert.ok(usd.ok, "a legacy request");
  assert.deepEqual([usd.value.items[0].unit, usd.value.items[0].regime, usd.value.items[0].charged], ["USD", "legacy_usd", "0.00019660"]);
  const mislabelled = await reads.jobs({ limit: 5, cursor: null });
  assert.equal(mislabelled.ok ? null : mislabelled.error.code, "internal_error");
  const outside = await reads.jobs({ limit: 5, cursor: null });
  assert.equal(outside.ok ? null : outside.error.code, "internal_error", "a hold state outside its vocabulary");
  const pending = await reads.jobs({ limit: 5, cursor: null });
  assert.ok(pending.ok, "a running request");
  assert.equal(pending.value.items[0].charged, null, "an unsettled job has no charge, not a zero");
  assert.equal(pending.value.items[0].resultAvailable, false);
});

test(T.spent, async () => {
  const { api } = scripted({ "/console/v1/credits": [answer(200, { ...CREDITS, spent: null }), answer(200, { ...CREDITS, spent: { amount: "1.00000000", unit: "USD" } })] });
  const reads = apiCreditReads(api);
  const unknown = await reads.wallet();
  assert.ok(unknown.ok && unknown.value !== null && unknown.value.spent === null, "the API could not say: unknown, not zero");
  const relabelled = await reads.wallet();
  assert.equal(relabelled.ok ? null : relabelled.error.code, "internal_error");
});

test(T.legacy, async () => {
  const statement = { balance: { amount: "4.99980340", unit: "USD" }, entry_count: 3, rollout_hold: true, as_of: "2026-09-25T12:00:00+00:00" };
  const { api, sent } = scripted({ "/console/v1/legacy-statement": [answer(200, statement), answer(200, { ...statement, balance: C("1.00000000") })] });
  const reads = apiCreditReads(api);
  assert.deepEqual(await reads.legacyUsd(), { ok: true, value: { balance: "4.99980340", entryCount: 3, rolloutHold: true } });
  assert.deepEqual(sent.map((s) => s.path), ["/console/v1/legacy-statement"], "no organization argument: the session's own");
  const wrong = await reads.legacyUsd();
  assert.equal(wrong.ok ? null : wrong.error.code, "internal_error");
});

test(T.cap, async () => {
  const { api, sent } = scripted({ "/console/v1/credit-ledger": [answer(200, { data: [], next_cursor: "n" })], "/console/v1/requests": [answer(200, { data: [], next_cursor: "n" })] });
  const reads = apiCreditReads(api);
  for (const limit of [0, 101, 2.5]) {
    for (const read of [reads.ledger({ limit, cursor: null }), reads.jobs({ limit, cursor: null })]) {
      const got = await read;
      assert.equal(got.ok ? null : got.error.code, "invalid_request", `limit ${limit}`);
    }
  }
  assert.equal(sent.length, 0, "refused before the call");
  const full = await reads.ledger({ limit: 100, cursor: null });
  assert.ok(full.ok && full.value.next_cursor === "n", "a full page at the cap keeps the API's cursor");
  const jobs = await reads.jobs({ limit: 100, cursor: null });
  assert.ok(jobs.ok && jobs.value.next_cursor === "n");
  assert.deepEqual(sent.map((s) => s.path), ["/console/v1/credit-ledger?limit=100", "/console/v1/requests?limit=100"]);
});
