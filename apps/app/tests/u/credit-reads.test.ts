// node --test "tests/**/*.test.ts"
//
// U1R: the consumer CREDIT read adapter over PostgREST (`app/(console)/billing/credit-reads.ts`).
// The client is a recording double of the supabase-js calls the adapter makes, so each case pins
// what reaches the database (which relation, which filters) and what a database answer becomes.
// The same row parsers run against real PostgreSQL rows in `credit-pg.test.ts`.
import assert from "node:assert/strict";
import test from "node:test";

import {
  CREDITS_IN_BOUND,
  postgrestCreditReads,
  type Answer,
  type CreditClient,
} from "../../app/(console)/billing/credit-reads.ts";

const USER = "c1000000-0000-4000-8000-000000000001";
const WALLET = "a1000000-0000-4000-8000-00000000000a";
const ORG = "0a000000-0000-4000-8000-0000000000aa";

type Call = { relation?: string; fn?: string; args?: unknown; ops: [string, ...unknown[]][] };

/** Records every call; answers from `script` by relation/function name, in order. */
function recording(script: Record<string, (Answer | Error)[]>) {
  const calls: Call[] = [];
  const next = (name: string): Promise<Answer> => {
    const queue = script[name];
    if (queue === undefined || queue.length === 0) throw new Error(`unscripted call to ${name}`);
    const answer = queue.shift()!;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
  };
  const builder = (call: Call, name: string) => {
    const chain = {
      eq: (...a: unknown[]) => (call.ops.push(["eq", ...a]), chain),
      neq: (...a: unknown[]) => (call.ops.push(["neq", ...a]), chain),
      or: (...a: unknown[]) => (call.ops.push(["or", ...a]), chain),
      order: (...a: unknown[]) => (call.ops.push(["order", ...a]), chain),
      limit: (...a: unknown[]) => (call.ops.push(["limit", ...a]), chain),
      then: (ok: (a: Answer) => unknown, bad?: (e: unknown) => unknown) => next(name).then(ok, bad),
    };
    return chain;
  };
  const client = {
    from(relation: string) {
      return {
        select(columns: string) {
          const call: Call = { relation, ops: [["select", columns]] };
          calls.push(call);
          return builder(call, relation);
        },
      };
    },
    rpc(fn: string, args: Record<string, unknown>) {
      const call: Call = { fn, args, ops: [] };
      calls.push(call);
      return builder(call, fn);
    },
  } as unknown as CreditClient;
  return { client, calls };
}

const ok = (data: unknown): Answer => ({ data, error: null });
const failed = (code: string, message = "x"): Answer => ({ data: null, error: { code, message } });

const walletRow = {
  wallet_id: WALLET,
  org_id: ORG,
  ledger_total: "9987.65432100",
  reserved_total: "12.00000000",
  available: "9975.65432100",
  revision: 7,
  signup_granted_at: "2026-09-20T12:00:00.123456+00:00",
};

function jobRow(over: Record<string, unknown> = {}) {
  return {
    request_id: "b1000000-0000-4000-8000-000000000001",
    job_handle: "job_x",
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
    unit: "CREDIT",
    hold: "5.00000000",
    hold_state: "settled",
    charged: "1.23456789",
    result_available: true,
    result_expires_at: "2026-09-20T12:10:00+00:00",
    settled_at: "2026-09-20T12:00:05+00:00",
    cursor: "2026-09-20 12:00:00+00|b1000000-0000-4000-8000-000000000001",
    ...over,
  };
}

const T = {
  wallet: "U1R-R01 the wallet is the caller's own consumer wallet, read exactly, and none is an explicit state",
  ledgerScope: "U1R-R02 the ledger is filtered to the caller's wallet, keyset-ordered, and a forged cursor never reaches a filter",
  jobs: "U1R-R03 jobs page through consumer_jobs on its own cursor, one extra row decides the next page",
  unavailable: "U1R-R04 an error, a transport failure or an inexact row is an explicit failure, never a zero",
  units: "U1R-R05 a job's unit follows its regime, and a CREDIT row labelled USD is refused",
  creditsIn: "U1R-R06 credits-in is a bounded read of the non-debit entries, and past the bound it is unknown",
  legacy: "U1R-R07 the legacy USD statement is read for the wallet's personal organization, in USD",
};

test(T.wallet, async () => {
  const { client, calls } = recording({ console_credit_wallets: [ok([walletRow]), ok([])] });
  const reads = postgrestCreditReads(client, USER);
  const got = await reads.wallet();
  assert.deepEqual(got, {
    ok: true,
    value: {
      walletId: WALLET,
      orgId: ORG,
      ledgerTotal: "9987.65432100",
      reservedTotal: "12.00000000",
      available: "9975.65432100",
      signupGrantedAt: "2026-09-20T12:00:00.123456Z",
    },
  });
  assert.equal(calls[0].relation, "console_credit_wallets");
  // An operator's session sees every wallet through the view: the owner filter is what scopes it.
  assert.deepEqual(
    calls[0].ops.filter(([op]) => op === "eq"),
    [["eq", "owner_user_id", USER], ["eq", "kind", "consumer"]],
  );
  assert.deepEqual(await reads.wallet(), { ok: true, value: null });
});

test(T.ledgerScope, async () => {
  const entry = (n: number) => ({
    entry_id: `e1000000-0000-4000-8000-00000000000${n}`,
    created_at: "2026-09-20T12:00:00+00:00",
    kind: "inference_debit",
    amount: "-1.00000000",
    unit: "CREDIT",
    request_id: `b1000000-0000-4000-8000-00000000000${n}`,
    reason: "inference",
  });
  const { client, calls } = recording({
    console_credit_ledger: [ok([entry(1), entry(2), entry(3)]), ok([entry(3)])],
  });
  const reads = postgrestCreditReads(client, USER);
  const first = await reads.ledger(WALLET, { limit: 2, cursor: null });
  assert.ok(first.ok);
  assert.equal(first.value.items.length, 2, "the extra row is a probe, not an item");
  assert.equal(first.value.items[0].amount, "-1.00000000");
  assert.equal(first.value.items[0].requestId, "b1000000-0000-4000-8000-000000000001");
  assert.notEqual(first.value.next_cursor, null);
  assert.deepEqual(calls[0].ops.find(([op]) => op === "eq"), ["eq", "wallet_id", WALLET]);
  // No `actor`: the view computes visible_principal() for it on EVERY wallet row before the sort
  // and limit (1.5 s a page at 10k entries, 8 ms without), and a consumer only ever sees `platform`.
  assert.deepEqual(calls[0].ops[0], ["select", "entry_id, created_at, kind, amount, unit, request_id, reason"]);
  assert.deepEqual(calls[0].ops.find(([op]) => op === "limit"), ["limit", 3]);
  assert.deepEqual(
    calls[0].ops.filter(([op]) => op === "order"),
    [["order", "created_at", { ascending: false }], ["order", "entry_id", { ascending: false }]],
  );
  const second = await reads.ledger(WALLET, { limit: 2, cursor: first.value.next_cursor });
  assert.ok(second.ok);
  assert.equal(second.value.next_cursor, null);
  const keyset = calls[1].ops.find(([op]) => op === "or");
  assert.deepEqual(keyset, [
    "or",
    'created_at.lt."2026-09-20T12:00:00.000000Z",and(created_at.eq."2026-09-20T12:00:00.000000Z",entry_id.lt.e1000000-0000-4000-8000-000000000002)',
  ]);
  // A cursor from a URL is untrusted: anything but the shape this adapter mints is refused before
  // the client is called, so it can never widen the PostgREST filter it would be spliced into.
  for (const forged of [
    "2026-09-20T12:00:00.000000Z|x),or(wallet_id.neq.0",
    "2026-09-20T12:00:00.000000Z|e1000000-0000-4000-8000-000000000002,created_at.gt.0",
    "",
  ]) {
    const before = calls.length;
    const refused = await reads.ledger(WALLET, { limit: 2, cursor: forged });
    assert.deepEqual(refused.ok ? null : refused.error.code, "invalid_cursor", forged);
    assert.equal(calls.length, before, "a forged cursor reached the client");
  }
});

test(T.jobs, async () => {
  const rows = [1, 2, 3].map((n) =>
    jobRow({ request_id: `b1000000-0000-4000-8000-00000000000${n}`, cursor: `c${n}` }),
  );
  const { client, calls } = recording({ consumer_jobs: [ok(rows), ok(rows.slice(0, 1))] });
  const reads = postgrestCreditReads(client, USER);
  const page = await reads.jobs({ limit: 2, cursor: null });
  assert.ok(page.ok);
  assert.equal(page.value.items.length, 2);
  assert.equal(page.value.next_cursor, "c2", "the next page resumes after the last row shown");
  assert.deepEqual(calls[0].args, { p_after: null, p_limit: 3 });
  const job = page.value.items[0];
  assert.equal(job.unit, "CREDIT");
  assert.equal(job.charged, "1.23456789");
  assert.equal(job.createdAt, "2026-09-20T12:00:00.000000Z");
  const last = await reads.jobs({ limit: 2, cursor: "c2" });
  assert.ok(last.ok);
  assert.deepEqual(calls[1].args, { p_after: "c2", p_limit: 3 });
  assert.equal(last.value.next_cursor, null);
});

test(T.unavailable, async () => {
  const { client } = recording({
    console_credit_wallets: [
      failed("PGRST301"),
      new Error("fetch failed"),
      ok([{ ...walletRow, ledger_total: 9987.654321 }]),
      ok([{ ...walletRow, available: "1e3" }]),
      ok([walletRow, walletRow]),
    ],
    consumer_jobs: [failed("P0001", "invalid_cursor: not a cursor this read issued"), failed("42501")],
  });
  const reads = postgrestCreditReads(client, USER);
  const codes = [];
  for (let n = 0; n < 5; n += 1) {
    const got = await reads.wallet();
    assert.equal(got.ok, false, `wallet read ${n} produced a value`);
    if (!got.ok) codes.push(got.error.code);
  }
  assert.deepEqual(codes, [
    "dependency_unavailable",
    "dependency_unavailable",
    "internal_error",
    "internal_error",
    "internal_error",
  ]);
  const stale = await reads.jobs({ limit: 2, cursor: "c9" });
  assert.equal(stale.ok ? null : stale.error.code, "invalid_cursor");
  const denied = await reads.jobs({ limit: 2, cursor: null });
  assert.equal(denied.ok ? null : denied.error.code, "forbidden");
});

test(T.units, async () => {
  const { client } = recording({
    consumer_jobs: [
      ok([jobRow({ accounting_regime: "legacy_usd", unit: "USD", charged: "0.00019660" })]),
      ok([jobRow({ unit: "USD" })]),
      ok([jobRow({ charged: 1.5 })]),
      ok([jobRow({ settlement_state: null, hold_state: "held", charged: null, prompt_tokens: null, completion_tokens: null, usage_certainty: null })]),
    ],
  });
  const reads = postgrestCreditReads(client, USER);
  const usd = await reads.jobs({ limit: 5, cursor: null });
  assert.ok(usd.ok);
  assert.equal(usd.value.items[0].unit, "USD");
  assert.equal(usd.value.items[0].regime, "legacy_usd");
  const mislabelled = await reads.jobs({ limit: 5, cursor: null });
  assert.equal(mislabelled.ok ? null : mislabelled.error.code, "internal_error");
  const float = await reads.jobs({ limit: 5, cursor: null });
  assert.equal(float.ok ? null : float.error.code, "internal_error");
  const pending = await reads.jobs({ limit: 5, cursor: null });
  assert.ok(pending.ok);
  assert.equal(pending.value.items[0].charged, null, "an unsettled job has no charge, not a zero");
  assert.equal(pending.value.items[0].promptTokens, null);
});

test(T.creditsIn, async () => {
  const entries = (n: number) => Array.from({ length: n }, () => ({ amount: "10000.00000000" }));
  const { client, calls } = recording({
    console_credit_ledger: [
      ok([{ amount: "10000.00000000" }, { amount: "-2.50000000" }]),
      ok(entries(CREDITS_IN_BOUND + 1)),
    ],
  });
  const reads = postgrestCreditReads(client, USER);
  assert.deepEqual(await reads.creditsIn(WALLET), { ok: true, value: "9997.50000000" });
  assert.deepEqual(
    calls[0].ops.filter(([op]) => op !== "select"),
    [["eq", "wallet_id", WALLET], ["neq", "kind", "inference_debit"], ["limit", CREDITS_IN_BOUND + 1]],
  );
  assert.deepEqual(await reads.creditsIn(WALLET), { ok: true, value: null });
});

test(T.legacy, async () => {
  const { client, calls } = recording({
    console_legacy_usd_statement: [
      ok([{ org_id: ORG, accounting_regime: "legacy_usd", unit: "USD", balance: "4.99980340", entry_count: 3, as_of: "2026-09-25T12:00:00+00:00", rollout_hold: true }]),
      ok([{ org_id: ORG, accounting_regime: "legacy_usd", unit: "CREDIT", balance: "1.00000000", entry_count: 1, as_of: "2026-09-25T12:00:00+00:00", rollout_hold: true }]),
    ],
  });
  const reads = postgrestCreditReads(client, USER);
  assert.deepEqual(await reads.legacyUsd(ORG), {
    ok: true,
    value: { balance: "4.99980340", entryCount: 3, rolloutHold: true },
  });
  assert.deepEqual(calls[0].args, { p_org: ORG });
  const wrong = await reads.legacyUsd(ORG);
  assert.equal(wrong.ok ? null : wrong.error.code, "internal_error");
});
