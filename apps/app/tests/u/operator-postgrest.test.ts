// node --test "tests/**/*.test.ts"   (skips visibly without the stack)
//
// U3 DUR-RLS / DUR-CAP / CONSOLE-FLOWS through the REAL path: the pinned Supabase PostgreSQL with
// every committed migration plus WR-U3-1's proposed operator SQL, PostgREST v13.0.4, supabase-js
// and signed JWTs. The App's own `operatorReads` and `operatorRpcPort` run unchanged, and the
// operator action runs through C3A's `consoleActions` composed exactly as WR-U3-2 wires it.
//
//     cd apps/infrx-api && INFRX_D_TASK=app-u3 INFRX_D1_IMAGE=supabase \
//         uv run --frozen python ../app/tests/u/operator_stack.py
//
// The stack script seeds the world, runs this file with INFRX_U3_STACK pointing at its manifest,
// then reads the durable effects back. Without it every case SKIPS; it never passes vacuously.
// The cases run in order and share the world (one database, one sequence of operator changes).

import assert from "node:assert/strict";
import { createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import type { Result } from "../../lib/contracts/types.ts";
import type { Credit } from "../../lib/contracts/v2/money-units.ts";
import { consoleActions, type ActionDeps, type OperatorCommand } from "../../lib/services/actions.ts";
import { operatorRpcPort, type OperatorRpcClient } from "../../app/(console)/admin/operator-port.ts";
import { operatorReads, type OperatorView, type ReadClient } from "../../app/(console)/admin/operator-reads.ts";

type Stack = {
  url: string;
  jwt_secret: string;
  users: Record<"c1" | "c2" | "operator", string>;
  orgs: Record<"c1" | "c2", string>;
  keys: Record<"c1" | "c2" | "c2_operator", string>;
  unknown_request: string;
};

const manifest = process.env.INFRX_U3_STACK;
const stack: Stack | null = manifest ? (JSON.parse(readFileSync(manifest, "utf8")) as Stack) : null;
const skip = stack === null ? "needs the task-local stack: run tests/u/operator_stack.py" : false;
const S = stack as Stack;
const PLACEHOLDER = "http://u3-stack.invalid";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = encode({ alg: "HS256", typ: "JWT" });
  const body = encode({ ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  const signature = createHmac("sha256", S.jwt_secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${signature}`;
}

/** A supabase-js client as one principal: an individual or operator (`sub`), anonymous, or the service role. */
function clientAs(who: string | "anon" | "service_role") {
  const token =
    who === "anon" ? jwt({ role: "anon" }) : who === "service_role" ? jwt({ role: "service_role" }) : jwt({ role: "authenticated", sub: who });
  return createClient(PLACEHOLDER, token, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: {
      fetch: (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return fetch(String(input).replace(`${PLACEHOLDER}/rest/v1`, S.url), { ...init, headers });
      },
    },
  });
}

const readsAs = (who: string) => operatorReads(clientAs(who) as unknown as ReadClient);
const portAs = (who: string) => operatorRpcPort(async () => clientAs(who) as unknown as OperatorRpcClient);
const audited = (key: string) => ({ reason: "U3 stack case", idempotency_key: key, actor: "operator:from-a-form" });

function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}
const codeOf = (result: Result<unknown>) => (result.ok ? "ok" : result.error.code);

async function accountOf(user: string) {
  const accounts = valueOf((await readsAs(S.users.operator)).accounts, "operator accounts");
  const account = accounts.find((a) => a.userId === user);
  assert.ok(account, `the operator sees ${user}'s wallet`);
  return account;
}

/** Exact CREDIT arithmetic in 1e-8 units: no float touches a balance. */
const ZERO = BigInt(0);
const units = (value: string) => BigInt(value.replace(".", ""));
const text = (u: bigint) => {
  const s = (u < ZERO ? -u : u).toString().padStart(9, "0");
  return `${u < ZERO ? "-" : ""}${s.slice(0, -8)}.${s.slice(-8)}`;
};

test("U3-DB01 the operator reads every section through its own JWT: exact CREDIT, the unknown-usage queue, no drift", { skip }, async () => {
  const view: OperatorView = await readsAs(S.users.operator);
  const accounts = valueOf(view.accounts, "accounts");
  for (const user of [S.users.c1, S.users.c2]) {
    const account = accounts.find((a) => a.userId === user);
    assert.ok(account, `wallet of ${user}`);
    assert.match(account.ledgerTotal, /^\d+\.\d{8}$/);
    assert.equal(units(account.available), units(account.ledgerTotal) - units(account.reservedTotal));
    assert.equal(account.suspended, false);
  }
  const held = valueOf(view.unknownUsage, "unknown usage");
  const unknown = held.find((h) => h.requestId === S.unknown_request);
  assert.ok(unknown, "the unknown-usage request is in the queue");
  assert.equal(unknown.orgId, S.orgs.c1);
  assert.equal(unknown.hold?.unit, "CREDIT");
  assert.deepEqual(valueOf(view.drift, "drift"), [], "WR-U3-1's drift view answers, and nothing drifts");
  valueOf(view.audit, "audit");
});

test("U3-DB02 DUR-RLS: a consumer's JWT reads no operator data (the page fails closed); anon reads nothing", { skip }, async () => {
  const mine = await readsAs(S.users.c1);
  assert.deepEqual(valueOf(mine.audit, "consumer audit"), [], "operator_audit is operator-only");
  assert.deepEqual(valueOf(mine.drift, "consumer drift"), []);
  assert.deepEqual(valueOf(mine.unknownUsage, "consumer unknown usage"), [], "the queue is operator-only, even the consumer's own row");
  // The organizations view is operator-only, so the accounts join cannot complete: unavailable, not a partial table.
  assert.equal(mine.accounts.ok, false);
  const c1 = clientAs(S.users.c1);
  const wallets = await c1.from("console_credit_wallets").select("owner_user_id");
  assert.deepEqual(wallets.data, [{ owner_user_id: S.users.c1 }], "a consumer sees its own wallet only");
  assert.deepEqual((await c1.from("console_admin_orgs").select("org_id")).data, []);
  const anon = await readsAs("anon");
  for (const section of [anon.accounts, anon.audit, anon.drift, anon.unknownUsage]) {
    assert.ok(!section.ok || (section.value as unknown[]).length === 0, "anon reads nothing");
  }
});

test("U3-DB03 DUR-RLS: consumer, provider-less member, anon and service-role sessions cannot run an operator change", { skip }, async () => {
  const before = await accountOf(S.users.c1);
  const commands: OperatorCommand[] = [
    { action: "adjust_credit", user_id: S.users.c1, amount: "1000000.00000000" as Credit, ...audited(`deny-adj-${randomUUID()}`) },
    { action: "set_suspension", org_id: S.orgs.c2, suspended: true, ...audited(`deny-sus-${randomUUID()}`) },
    { action: "revoke_key", key_id: S.keys.c2, ...audited(`deny-rev-${randomUUID()}`) },
  ];
  for (const who of [S.users.c1, S.users.c2, "anon", "service_role"]) {
    for (const command of commands) {
      const code = codeOf(await portAs(who).run(command));
      assert.ok(code === "forbidden", `${who} ${command.action}: ${code}`);
    }
  }
  const after = await accountOf(S.users.c1);
  assert.equal(after.ledgerTotal, before.ledgerTotal, "no credit was minted");
  assert.equal((await accountOf(S.users.c2)).suspended, false, "nobody was suspended");
});

test("U3-DB04 an adjustment commits once: a replay is 'already applied', another amount under the key is a conflict", { skip }, async () => {
  const before = await accountOf(S.users.c1);
  const port = portAs(S.users.operator);
  const key = `adj-${randomUUID()}`;
  const command: OperatorCommand = { action: "adjust_credit", user_id: S.users.c1, amount: "250.00000000" as Credit, ...audited(key) };
  assert.deepEqual(await port.run(command), { ok: true, value: { replayed: false } });
  assert.deepEqual(await port.run(command), { ok: true, value: { replayed: true } });
  assert.equal(codeOf(await port.run({ ...command, amount: "251.00000000" as Credit })), "idempotency_conflict");
  assert.equal(codeOf(await port.run({ ...command, user_id: S.users.c2 })), "idempotency_conflict");
  const after = await accountOf(S.users.c1);
  assert.equal(units(after.ledgerTotal) - units(before.ledgerTotal), units("250.00000000"), "exactly one +250");
  assert.equal(codeOf(await port.run({ ...command, amount: "0.00000000" as Credit, idempotency_key: `zero-${randomUUID()}` })), "invalid_request");
  assert.equal(
    codeOf(await port.run({ ...command, user_id: "0b300000-0000-4000-8000-0000000000ff", idempotency_key: `nobody-${randomUUID()}` })),
    "not_found",
  );
});

test("U3-DB05 DUR-CAP: concurrent retries apply once; concurrent corrections never take available below zero", { skip }, async () => {
  const port = portAs(S.users.operator);
  const before = await accountOf(S.users.c1);
  const same: OperatorCommand = { action: "adjust_credit", user_id: S.users.c1, amount: "1.00000000" as Credit, ...audited(`race-${randomUUID()}`) };
  const retries = await Promise.all(Array.from({ length: 8 }, () => port.run(same)));
  const replays = retries.map((r) => valueOf(r, "a concurrent retry").replayed);
  assert.equal(replays.filter((r) => !r).length, 1, `exactly one applied: ${JSON.stringify(replays)}`);
  const mid = await accountOf(S.users.c1);
  assert.equal(units(mid.ledgerTotal) - units(before.ledgerTotal), units("1.00000000"));

  // Each correction takes more than half of what is available: at most one can fit.
  const take = text(-(units(mid.available) / BigInt(2) + units("1.00000000")));
  const corrections = await Promise.all(
    Array.from({ length: 5 }, () => port.run({ ...same, amount: take as Credit, idempotency_key: `take-${randomUUID()}` })),
  );
  const codes = corrections.map(codeOf);
  assert.equal(codes.filter((c) => c === "ok").length, 1, `one correction fits: ${codes.join(",")}`);
  assert.ok(codes.every((c) => c === "ok" || c === "invalid_request"), codes.join(","));
  const after = await accountOf(S.users.c1);
  assert.ok(units(after.available) >= ZERO, `available ${after.available}`);
  assert.equal(units(after.ledgerTotal), units(mid.ledgerTotal) + units(take));
  // Restore the room the later cases (and the unknown-usage hold) rely on.
  valueOf(await port.run({ ...same, amount: text(-units(take)) as Credit, idempotency_key: `restore-${randomUUID()}` }), "restore");
});

test("U3-DB06 suspension and restore are audited, replayable and conflict-checked", { skip }, async () => {
  const port = portAs(S.users.operator);
  const key = `sus-${randomUUID()}`;
  const suspend: OperatorCommand = { action: "set_suspension", org_id: S.orgs.c2, suspended: true, ...audited(key) };
  assert.deepEqual(await port.run(suspend), { ok: true, value: { replayed: false } });
  const racing = await Promise.all(Array.from({ length: 4 }, () => port.run(suspend)));
  assert.ok(racing.every((r) => r.ok && r.value.replayed), JSON.stringify(racing));
  assert.equal(codeOf(await port.run({ ...suspend, suspended: false })), "idempotency_conflict");
  assert.equal(codeOf(await port.run({ ...suspend, org_id: S.orgs.c1 })), "idempotency_conflict");
  const suspended = await accountOf(S.users.c2);
  assert.equal(suspended.suspended, true);
  assert.equal(suspended.suspensionReason, "other");
  assert.deepEqual(await port.run({ ...suspend, suspended: false, idempotency_key: `restore-${randomUUID()}` }), { ok: true, value: { replayed: false } });
  assert.equal((await accountOf(S.users.c2)).suspended, false);
  assert.equal(codeOf(await port.run({ ...suspend, org_id: "0b300000-0000-4000-8000-0000000000ff", idempotency_key: `ghost-${randomUUID()}` })), "not_found");
});

test("U3-DB07 a consumer key is revoked once; operator-audience keys and unknown ids are not found", { skip }, async () => {
  const port = portAs(S.users.operator);
  const revoke: OperatorCommand = { action: "revoke_key", key_id: S.keys.c1, ...audited(`rev-${randomUUID()}`) };
  const racing = await Promise.all(Array.from({ length: 4 }, () => port.run(revoke)));
  assert.equal(racing.filter((r) => r.ok && !r.value.replayed).length, 1, JSON.stringify(racing));
  assert.ok(racing.every((r) => r.ok));
  assert.deepEqual(await port.run(revoke), { ok: true, value: { replayed: true } });
  assert.equal(codeOf(await port.run({ ...revoke, key_id: S.keys.c2 })), "idempotency_conflict");
  assert.equal(codeOf(await port.run({ ...revoke, key_id: S.keys.c2_operator, idempotency_key: `op-${randomUUID()}` })), "not_found");
  assert.equal(codeOf(await port.run({ ...revoke, key_id: "0b300000-0000-4000-8000-0000000000ff", idempotency_key: `x-${randomUUID()}` })), "not_found");
  const { data } = await clientAs(S.users.c1).from("api_keys").select("id,revoked_at").eq("id", S.keys.c1);
  assert.ok((data as { revoked_at: string | null }[])[0].revoked_at !== null, "the owner sees the key revoked");
});

test("U3-DB08 the audit trail names the operator (from the JWT, never the form) with the reason", { skip }, async () => {
  const entries = valueOf((await readsAs(S.users.operator)).audit, "audit");
  const mine = entries.filter((e) => e.reason === "U3 stack case");
  const actions = new Set(mine.map((e) => e.action));
  for (const action of ["admin_adjust", "admin_set_suspension", "admin_key_revoke"] as const) assert.ok(actions.has(action), action);
  assert.ok(mine.every((e) => e.actor === `operator:${S.users.operator}`), JSON.stringify(mine.map((e) => e.actor)));
});

test("U3-DB09 WR-U3-2 composed: C3A's consoleActions with this port - origin, then operator authority, then one audited change", { skip }, async () => {
  const deps = (who: string, isOperator: boolean, origin = "https://app.example.com"): ActionDeps => ({
    headers: async () => new Headers({ origin, host: "app.example.com" }),
    context: async () => ({ state: "signed_out" }),
    keys: async () => assert.fail("no key store on the operator path"),
    session: async () => ({ userId: who, isOperator }),
    endSession: async () => {},
    revalidate: () => {},
    operator: operatorRpcPort(async () => clientAs(who) as unknown as OperatorRpcClient),
  });
  const input = { action: "adjust_credit", user_id: S.users.c2, amount: "3.00000000", reason: "U3 composed", idempotency_key: `wr-${randomUUID()}` };
  const before = await accountOf(S.users.c2);
  assert.equal(codeOf(await consoleActions(deps(S.users.operator, true, "https://evil.example")).operator(input)), "forbidden");
  assert.equal(codeOf(await consoleActions(deps(S.users.c2, false)).operator(input)), "forbidden");
  // A session that claims operator authority the database does not grant is still refused (42501).
  assert.equal(codeOf(await consoleActions(deps(S.users.c2, true)).operator(input)), "forbidden");
  assert.equal((await accountOf(S.users.c2)).ledgerTotal, before.ledgerTotal);
  assert.deepEqual(await consoleActions(deps(S.users.operator, true)).operator(input), { ok: true, value: { replayed: false } });
  assert.deepEqual(await consoleActions(deps(S.users.operator, true)).operator(input), { ok: true, value: { replayed: true } });
  assert.equal(units((await accountOf(S.users.c2)).ledgerTotal) - units(before.ledgerTotal), units("3.00000000"));
});
