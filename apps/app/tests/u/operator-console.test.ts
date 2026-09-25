// node --test "tests/**/*.test.ts"
//
// U3: minimal operator controls (`app/(console)/admin/`). The operator surface is a protected
// operations view, not the provider Lab. What these cases pin:
//
// - there is no arbitrary balance edit any more: the legacy `addCredit` (a free-form USD row
//   written with the service key) is gone, and nothing in admin/ holds a service key;
// - the page answers 404 to anyone without operator authority BEFORE it reads anything;
// - the operator port sends exactly the audited operation (who acts is decided by the database
//   from the operator's own JWT, never by a field), and maps every refusal to a typed code with
//   fixed text - a DB message never reaches the page, and an answer it cannot read is "not
//   confirmed", never a success;
// - the reads are exact CREDIT decimal strings or an explicit unavailable state per section, never
//   a zero, a number or a fixture;
// - a form keeps its idempotency key across every failure (a retry cannot apply twice) and rotates
//   it only after a committed change.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type { OperatorCommand } from "../../lib/services/actions.ts";
import type { Credit } from "../../lib/contracts/v2/money-units.ts";
import { OPERATOR_RPC, operatorRpcPort, type OperatorRpcClient } from "../../app/(console)/admin/operator-port.ts";
import {
  ACCOUNT_LIMIT,
  AUDIT_LIMIT,
  OPERATOR_RELATIONS,
  UNKNOWN_LIMIT,
  operatorReads,
  type ReadClient,
} from "../../app/(console)/admin/operator-reads.ts";
import { OPERATOR_FORMS, formInput, nextKey, outcomeOf } from "../../app/(console)/admin/operator-form.ts";

const ADMIN = join(import.meta.dirname, "../../app/(console)/admin");
const USER = "c1000000-0000-4000-8000-000000000001";
const ORG = "0a000000-0000-4000-8000-0000000000aa";
const WALLET = "a1000000-0000-4000-8000-00000000000a";
const KEY_ID = "b1000000-0000-4000-8000-00000000000b";
const AUDITED = { reason: "support ticket 42", idempotency_key: "k-1", actor: "operator:someone" };

// ------------------------------------------------------------------------------------ surface

test("U3-S01 no arbitrary balance edit: admin/ writes no ledger row and holds no service key", () => {
  assert.equal(existsSync(join(ADMIN, "actions.ts")), false, "the legacy addCredit server action is deleted");
  for (const file of readdirSync(ADMIN)) {
    const source = readFileSync(join(ADMIN, file), "utf8");
    for (const banned of ["createAdminClient", "SUPABASE_SERVICE_ROLE_KEY", "credit_ledger", "delta_usd", ".insert(", ".update(", ".upsert(", ".delete("]) {
      assert.ok(!source.includes(banned), `${file} must not contain ${banned}`);
    }
  }
});

test("U3-S02 the page refuses a non-operator (404) before any read, and reads with the session's own client", () => {
  const page = readFileSync(join(ADMIN, "page.tsx"), "utf8");
  const gate = page.indexOf("if (!session.isOperator) notFound();");
  const read = page.indexOf("operatorReads(");
  assert.ok(gate > 0, "the operator gate is present");
  assert.ok(read > gate, "the reads run only after the gate");
  assert.match(page, /operatorReads\(\(await createClient\(\)\)/, "the reads use the cookie client (the operator's own JWT)");
  assert.ok(!/fixture/i.test(page), "no fixture reaches the operator page");
});

test("U3-S03 the forms call the shared audited server action and show no unsupported control", () => {
  const forms = readFileSync(join(ADMIN, "operator-forms.tsx"), "utf8");
  assert.match(forms, /^"use client";/);
  assert.match(forms, /import \{ operatorAction \} from "@\/app\/actions";/);
  assert.ok(!/grant_initial|publish|set_balance/.test(forms), "no control for an operation the console cannot perform");
  assert.deepEqual(
    OPERATOR_FORMS.map((form) => form.action),
    ["adjust_credit", "set_suspension", "revoke_key"],
  );
});

// --------------------------------------------------------------------------------------- port

type Call = { fn: string; args: Record<string, unknown> };
function rpcClient(answer: { data: unknown; error: { code?: string; message?: string } | null } | Error, calls: Call[] = []) {
  const client: OperatorRpcClient = {
    rpc(fn, args) {
      calls.push({ fn, args });
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    },
  };
  return { port: operatorRpcPort(async () => client), calls };
}

const ADJUST: OperatorCommand = { action: "adjust_credit", user_id: USER, amount: "-2.50000000" as Credit, ...AUDITED };
const SUSPEND: OperatorCommand = { action: "set_suspension", org_id: ORG, suspended: true, ...AUDITED };
const REVOKE: OperatorCommand = { action: "revoke_key", key_id: KEY_ID, ...AUDITED };

test("U3-P01 each command is exactly one audited RPC; the actor is never sent (the DB derives it)", async () => {
  const cases: [OperatorCommand, string, Record<string, unknown>][] = [
    [ADJUST, OPERATOR_RPC.adjust_credit, { p_user: USER, p_amount: "-2.50000000", p_reason: AUDITED.reason, p_idempotency_key: "k-1" }],
    [SUSPEND, OPERATOR_RPC.set_suspension, { p_org: ORG, p_suspended: true, p_reason: AUDITED.reason, p_idempotency_key: "k-1" }],
    [REVOKE, OPERATOR_RPC.revoke_key, { p_key: KEY_ID, p_reason: AUDITED.reason, p_idempotency_key: "k-1" }],
  ];
  for (const [command, fn, args] of cases) {
    const { port, calls } = rpcClient({ data: { replayed: false }, error: null });
    assert.deepEqual(await port.run(command), { ok: true, value: { replayed: false } });
    assert.deepEqual(calls, [{ fn, args }]);
    assert.ok(!JSON.stringify(calls).includes("operator:someone"), "the form-side actor never reaches the database");
  }
  assert.deepEqual(OPERATOR_RPC, {
    adjust_credit: "operator_adjust_credit",
    set_suspension: "operator_set_suspension",
    revoke_key: "operator_revoke_key",
  });
});

test("U3-P02 a replay is reported as a replay; an answer without a boolean is 'not confirmed', never success", async () => {
  assert.deepEqual(await rpcClient({ data: { replayed: true, amount: "5.00000000" }, error: null }).port.run(ADJUST), {
    ok: true,
    value: { replayed: true },
  });
  for (const data of [null, {}, { replayed: "false" }, [{ replayed: false }], "ok"]) {
    const result = await rpcClient({ data, error: null }).port.run(ADJUST);
    assert.equal(result.ok, false, `answer ${JSON.stringify(data)}`);
    if (!result.ok) assert.equal(result.error.code, "dependency_unavailable");
  }
});

test("U3-P03 the one-time signup grant is not an operator console operation: nothing is sent", async () => {
  const { port, calls } = rpcClient({ data: { replayed: false }, error: null });
  const result = await port.run({ action: "grant_initial", user_id: USER, ...AUDITED });
  assert.equal(calls.length, 0);
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "unsupported_parameter");
});

test("U3-P04 refusals map to typed codes with fixed text; DB detail never reaches the page", async () => {
  const secret = "relation infrx.credit_wallets wallet a1000000 row 7";
  const cases: [{ code?: string; message?: string }, string][] = [
    [{ code: "42501", message: `forbidden: ${secret}` }, "forbidden"],
    [{ code: "P0001", message: `idempotency_conflict: ${secret}` }, "idempotency_conflict"],
    [{ code: "P0001", message: `invalid_request: ${secret}` }, "invalid_request"],
    [{ code: "P0001", message: `not_found: ${secret}` }, "not_found"],
    [{ code: "P0001", message: `state_conflict: ${secret}` }, "state_conflict"],
    [{ code: "P0002", message: `not_found: ${secret}` }, "not_found"],
    [{ code: "P0001", message: `internal_error: ${secret}` }, "dependency_unavailable"],
    [{ code: "P0001", message: `made_up_code: ${secret}` }, "dependency_unavailable"],
    [{ code: "PGRST202", message: secret }, "dependency_unavailable"],
    [{ code: "23505", message: secret }, "dependency_unavailable"],
    [{ message: secret }, "dependency_unavailable"],
  ];
  for (const [error, code] of cases) {
    const result = await rpcClient({ data: null, error }).port.run(ADJUST);
    assert.equal(result.ok, false);
    if (result.ok) continue;
    assert.equal(result.error.code, code, JSON.stringify(error));
    assert.ok(!result.error.message.includes("a1000000") && !result.error.message.includes("infrx"), result.error.message);
  }
});

test("U3-P05 a transport failure or a client that cannot be built is 'not confirmed, retry with the same key'", async () => {
  for (const port of [
    rpcClient(new Error("fetch failed: 10.0.0.1")).port,
    operatorRpcPort(async () => {
      throw new Error("no cookie");
    }),
  ]) {
    const result = await port.run(SUSPEND);
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "dependency_unavailable");
      assert.match(result.error.message, /same idempotency key/);
    }
  }
});

// -------------------------------------------------------------------------------------- reads

type Answer = { data: unknown; error: { code?: string; message?: string } | null };
type Recorded = { relation: string; select: string; ops: [string, ...unknown[]][] };

function readClient(answers: Record<string, Answer>, recorded: Recorded[] = []): ReadClient {
  return {
    from(relation) {
      return {
        select(columns) {
          const entry: Recorded = { relation, select: columns, ops: [] };
          recorded.push(entry);
          const chain = {
            eq: (...a: unknown[]) => (entry.ops.push(["eq", ...a]), chain),
            in: (...a: unknown[]) => (entry.ops.push(["in", ...a]), chain),
            order: (...a: unknown[]) => (entry.ops.push(["order", ...a]), chain),
            limit: (...a: unknown[]) => (entry.ops.push(["limit", ...a]), chain),
            then<A, B>(ok?: ((v: Answer) => A | PromiseLike<A>) | null, bad?: ((e: unknown) => B | PromiseLike<B>) | null) {
              const answer = answers[relation];
              return (answer === undefined ? Promise.reject(new Error("no such relation")) : Promise.resolve(answer)).then(ok, bad);
            },
          };
          return chain;
        },
      };
    },
  };
}

const walletRow = (over: Record<string, unknown> = {}) => ({
  wallet_id: WALLET,
  kind: "consumer",
  owner_user_id: USER,
  org_id: ORG,
  ledger_total: "10000.00000000",
  reserved_total: "14.74560000",
  available: "9985.25440000",
  signup_granted_at: "2026-09-25T10:00:00+00:00",
  ...over,
});
const orgRow = (over: Record<string, unknown> = {}) => ({
  org_id: ORG,
  name: "Personal",
  owner_email: "person@example.com",
  suspended: false,
  suspension_reason: null,
  ...over,
});
const auditRow = (over: Record<string, unknown> = {}) => ({
  id: "e1000000-0000-4000-8000-00000000000e",
  at: "2026-09-25T11:00:00+00:00",
  actor_principal: "operator:33333333-3333-4333-8333-333333333333",
  action: "admin_adjust",
  target_org_id: ORG,
  reason: "support ticket 42",
  idempotency_key: "grant_credit:0f",
  ...over,
});
const heldRow = (over: Record<string, unknown> = {}) => ({
  request_id: "f1000000-0000-4000-8000-00000000000f",
  org_id: ORG,
  created_at: "2026-09-24T09:00:00+00:00",
  reconcile_after: "2026-09-25T09:00:00+00:00",
  unit: "CREDIT",
  hold: "14.74560000",
  ...over,
});
const WORLD = (): Record<string, Answer> => ({
  console_credit_wallets: { data: [walletRow()], error: null },
  console_admin_orgs: { data: [orgRow()], error: null },
  operator_unknown_usage: { data: [heldRow(), heldRow({ request_id: "f2000000-0000-4000-8000-00000000000f", unit: "USD", hold: "0.25000000" })], error: null },
  operator_audit: { data: [auditRow()], error: null },
  operator_wallet_drift: { data: [], error: null },
});

test("U3-R01 accounts are exact CREDIT strings joined to their organization's suspension state", async () => {
  const reads = await operatorReads(readClient(WORLD()));
  assert.deepEqual(reads.accounts, {
    ok: true,
    value: [
      {
        userId: USER,
        orgId: ORG,
        walletId: WALLET,
        email: "person@example.com",
        suspended: false,
        suspensionReason: null,
        ledgerTotal: "10000.00000000",
        reservedTotal: "14.74560000",
        available: "9985.25440000",
        signupGrantedAt: "2026-09-25T10:00:00+00:00",
      },
    ],
  });
});

test("U3-R02 a figure that is not an exact decimal string, or does not reconcile, makes the section unavailable - never a zero", async () => {
  for (const bad of [
    walletRow({ ledger_total: 10000 }),
    walletRow({ available: "1e4" }),
    walletRow({ ledger_total: null }),
    walletRow({ available: "9985.25440001" }),
    walletRow({ owner_user_id: null }),
  ]) {
    const world = WORLD();
    world.console_credit_wallets = { data: [bad], error: null };
    const reads = await operatorReads(readClient(world));
    assert.equal(reads.accounts.ok, false, JSON.stringify(bad));
    if (!reads.accounts.ok) assert.equal(reads.accounts.error.code, "dependency_unavailable");
    assert.equal(reads.audit.ok, true, "one broken section does not take the others down");
  }
  const orphan = WORLD();
  orphan.console_admin_orgs = { data: [], error: null };
  assert.equal((await operatorReads(readClient(orphan))).accounts.ok, false, "a wallet whose organization is not readable");
});

test("U3-R03 each section fails on its own: an error, a rejected read or a missing relation is unavailable", async () => {
  const world = WORLD();
  world.operator_unknown_usage = { data: null, error: { code: "42501", message: "permission denied" } };
  world.operator_wallet_drift = { data: null, error: { code: "PGRST205", message: "relation not found" } };
  delete (world as Partial<typeof world>).operator_audit;
  const reads = await operatorReads(readClient(world));
  assert.equal(reads.accounts.ok, true);
  for (const section of [reads.unknownUsage, reads.drift, reads.audit]) {
    assert.equal(section.ok, false);
    if (!section.ok) {
      assert.equal(section.error.code, "dependency_unavailable");
      assert.ok(!/permission|relation/.test(section.error.message), "no DB text");
    }
  }
});

test("U3-R04 the reads touch only the operator read surface, bounded, with the documented filters", async () => {
  const recorded: Recorded[] = [];
  await operatorReads(readClient(WORLD(), recorded));
  assert.deepEqual([...new Set(recorded.map((r) => r.relation))].sort(), [...OPERATOR_RELATIONS].sort());
  const of = (relation: string) => recorded.find((r) => r.relation === relation) as Recorded;
  assert.deepEqual(of("console_credit_wallets").ops, [
    ["eq", "kind", "consumer"],
    ["order", "updated_at", { ascending: false }],
    ["limit", ACCOUNT_LIMIT],
  ]);
  assert.deepEqual(of("console_admin_orgs").ops, [["in", "org_id", [ORG]]]);
  assert.deepEqual(of("operator_unknown_usage").ops, [
    ["order", "created_at", { ascending: true }],
    ["limit", UNKNOWN_LIMIT],
  ]);
  assert.deepEqual(of("operator_audit").ops, [
    ["order", "at", { ascending: false }],
    ["limit", AUDIT_LIMIT],
  ]);
  const empty = WORLD();
  empty.console_credit_wallets = { data: [], error: null };
  const none: Recorded[] = [];
  const reads = await operatorReads(readClient(empty, none));
  assert.deepEqual(reads.accounts, { ok: true, value: [] });
  assert.ok(!none.some((r) => r.relation === "console_admin_orgs"), "no organization read for no wallets");
});

test("U3-R05 an unknown-usage hold keeps its own unit: CREDIT for a credit job, USD for a legacy one", async () => {
  const reads = await operatorReads(readClient(WORLD()));
  const at = { orgId: ORG, createdAt: "2026-09-24T09:00:00+00:00", reconcileAfter: "2026-09-25T09:00:00+00:00" };
  assert.deepEqual(reads.unknownUsage, {
    ok: true,
    value: [
      { requestId: "f1000000-0000-4000-8000-00000000000f", ...at, hold: { amount: "14.74560000", unit: "CREDIT" } },
      { requestId: "f2000000-0000-4000-8000-00000000000f", ...at, hold: { amount: "0.25000000", unit: "USD" } },
    ],
  });
  const world = WORLD();
  world.operator_unknown_usage = { data: [heldRow({ hold: null })], error: null };
  assert.equal((await operatorReads(readClient(world))).unknownUsage.ok && (await operatorReads(readClient(world))).unknownUsage.value[0].hold, null);
  for (const bad of [heldRow({ hold: 14.7456 }), heldRow({ unit: "barter" }), heldRow({ reconcile_after: null })]) {
    world.operator_unknown_usage = { data: [bad], error: null };
    assert.equal((await operatorReads(readClient(world))).unknownUsage.ok, false, JSON.stringify(bad));
  }
});

test("U3-R06 the audit trail is the closed action vocabulary; drift rows are exact CREDIT", async () => {
  const reads = await operatorReads(readClient(WORLD()));
  assert.deepEqual(reads.audit, {
    ok: true,
    value: [
      {
        id: "e1000000-0000-4000-8000-00000000000e",
        at: "2026-09-25T11:00:00+00:00",
        actor: "operator:33333333-3333-4333-8333-333333333333",
        action: "admin_adjust",
        targetOrgId: ORG,
        reason: "support ticket 42",
        idempotencyKey: "grant_credit:0f",
      },
    ],
  });
  const world = WORLD();
  world.operator_audit = { data: [auditRow({ action: "set_balance" })], error: null };
  world.operator_wallet_drift = { data: [{ wallet_id: WALLET, kind: "consumer", ledger_drift: "0.00000001", reserved_drift: "0.00000000" }], error: null };
  const again = await operatorReads(readClient(world));
  assert.equal(again.audit.ok, false);
  assert.deepEqual(again.drift, { ok: true, value: [{ walletId: WALLET, kind: "consumer", ledgerDrift: "0.00000001", reservedDrift: "0.00000000" }] });
  world.operator_wallet_drift = { data: [{ wallet_id: WALLET, kind: "consumer", ledger_drift: 0, reserved_drift: "0.00000000" }], error: null };
  assert.equal((await operatorReads(readClient(world))).drift.ok, false);
});

// -------------------------------------------------------------------------------------- forms

test("U3-F01 a form keeps its idempotency key across every failure and rotates it only after a committed change", () => {
  const fresh = () => "k-2";
  assert.equal(nextKey("k-1", { ok: true, value: { replayed: false } }, fresh), "k-2");
  assert.equal(nextKey("k-1", { ok: true, value: { replayed: true } }, fresh), "k-2");
  for (const code of ["dependency_unavailable", "idempotency_conflict", "forbidden", "invalid_request", "internal_error"] as const) {
    assert.equal(nextKey("k-1", { ok: false, error: { code, message: "x" } }, fresh), "k-1", code);
  }
});

test("U3-F02 outcomes say what committed: once, already applied, refused, or not confirmed", () => {
  assert.deepEqual(outcomeOf({ ok: true, value: { replayed: false } }), { tone: "ok", text: "Committed. The change is recorded once in the audit trail." });
  assert.deepEqual(outcomeOf({ ok: true, value: { replayed: true } }), { tone: "ok", text: "Already applied: this request was recorded earlier, and nothing changed twice." });
  const conflict = outcomeOf({ ok: false, error: { code: "idempotency_conflict", message: "x" } });
  assert.equal(conflict.tone, "error");
  assert.match(conflict.text, /different change/);
  const unconfirmed = outcomeOf({ ok: false, error: { code: "dependency_unavailable", message: "the operator change could not be confirmed; retry with the same idempotency key" } });
  assert.equal(unconfirmed.tone, "error");
  assert.match(unconfirmed.text, /same idempotency key/);
  assert.match(outcomeOf({ ok: false, error: { code: "forbidden", message: "operator authority is required" } }).text, /operator authority/);
});

test("U3-F03 a form submits exactly the allowlisted fields of its operation, plus its key", () => {
  const fields = (entries: Record<string, string>) => (name: string) => entries[name] ?? null;
  assert.deepEqual(formInput("adjust_credit", fields({ user_id: USER, amount: "5.00000000", reason: "goodwill", org_id: ORG, actor: "me" }), "k-1"), {
    action: "adjust_credit",
    user_id: USER,
    amount: "5.00000000",
    reason: "goodwill",
    idempotency_key: "k-1",
  });
  assert.deepEqual(formInput("set_suspension", fields({ org_id: ORG, suspended: "true", reason: "abuse report" }), "k-1"), {
    action: "set_suspension",
    org_id: ORG,
    suspended: true,
    reason: "abuse report",
    idempotency_key: "k-1",
  });
  assert.equal((formInput("set_suspension", fields({ org_id: ORG, suspended: "false", reason: "resolved" }), "k-1") as { suspended: unknown }).suspended, false);
  assert.deepEqual(formInput("revoke_key", fields({ key_id: KEY_ID, reason: "leaked" }), "k-1"), {
    action: "revoke_key",
    key_id: KEY_ID,
    reason: "leaked",
    idempotency_key: "k-1",
  });
});
