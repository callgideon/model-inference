// node --test "tests/**/*.test.ts"   (skips visibly without the stack)
//
// C3A DUR-RLS / CONSOLE-FLOWS through the REAL path: the pinned Supabase PostgreSQL with every
// migration, PostgREST v13.0.4, supabase-js and signed JWTs, composed as `app/actions.ts` composes
// them - `supabaseKeyStore` over the individual's own client, the context from C0's
// `resolveConsumerContext` over the same individual's client. The grant cases call
// `claim_signup_grant` over a service-role client exactly as A2's `app/(auth)/grant.ts` does (the
// App's one grant adapter): they pin the DB boundary and the concurrent-callback race it relies on.
//
//     cd apps/infrx-api && INFRX_D_TASK=app-c3a INFRX_D1_IMAGE=supabase \
//         uv run --frozen python ../app/tests/c/realdb/actions_stack.py
//
// The stack script seeds the world and runs this file with INFRX_C3A_STACK pointing at its manifest.
// Without it every case SKIPS; it never passes vacuously.

import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import type { Result } from "../../lib/contracts/types.ts";
import { postgrestPort, type PostgrestClient } from "../../lib/services/query.ts";
import { resolveConsumerContext, type ConsumerContext } from "../../lib/services/console.ts";
import { createConsumerActions, supabaseKeyStore, type KeyClient } from "../../lib/services/actions.ts";

type Stack = {
  url: string;
  jwt_secret: string;
  users: Record<"c1" | "c2" | "shared" | "fresh" | "unverified", string>;
  orgs: Record<"c1" | "c2" | "shared" | "unverified", string>;
  keys: Record<"c1" | "c2" | "c2_operator", string>;
};

const manifest = process.env.INFRX_C3A_STACK;
const stack: Stack | null = manifest ? (JSON.parse(readFileSync(manifest, "utf8")) as Stack) : null;
const skip = stack === null ? "needs the task-local stack: run tests/c/realdb/actions_stack.py" : false;
const S = stack as Stack;
const PLACEHOLDER = "http://c3a-stack.invalid";
const CONFIRMED = "2026-09-25T12:00:00Z";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = encode({ alg: "HS256", typ: "JWT" });
  const body = encode({ ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  const signature = createHmac("sha256", S.jwt_secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${signature}`;
}

/** A supabase-js client as one principal: an individual (`sub`), anonymous, or the service role. */
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

/** C0's resolution, as the individual, from a GoTrue-shaped user. */
async function contextOf(user: string, verified = true): Promise<ConsumerContext> {
  const port = postgrestPort(clientAs(user) as unknown as PostgrestClient);
  return resolveConsumerContext(port, { id: user, email: `${user.slice(-4)}@example.com`, email_confirmed_at: verified ? CONFIRMED : null });
}

const storeAs = (user: string) => supabaseKeyStore(clientAs(user) as unknown as KeyClient);
/** A2's call (`app/(auth)/flow.ts` `claimArgs`): the session's user id and the campaign, nothing else. */
async function claim(user: string) {
  const { data, error } = await clientAs("service_role").rpc("claim_signup_grant", { p_user_id: user, p_campaign_version: "consumer-v1" });
  assert.equal(error, null);
  const rows = data as { status: string; user_id: string; amount: string | null }[];
  assert.equal(rows.length, 1);
  assert.equal(rows[0].user_id, user);
  return rows[0];
}

function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}

function codeOf(result: Result<unknown>): string {
  return result.ok ? "ok" : result.error.code;
}

async function keyRow(user: string, id: string) {
  const { data, error } = await clientAs(user).from("api_keys").select("id,org_id,audience,user_id,key_hash,revoked_at").eq("id", id);
  assert.equal(error, null);
  return (data ?? [])[0] as { org_id: string; audience: string; user_id: string; key_hash: string; revoked_at: string | null } | undefined;
}

test("DUR-RLS: only the service role can reach the grant; anon and an individual cannot self-grant", { skip }, async () => {
  for (const who of ["anon", S.users.fresh] as const) {
    const { error } = await clientAs(who).rpc("claim_signup_grant", { p_user_id: S.users.fresh });
    assert.ok(error !== null && error.code === "42501", `${who}: ${error?.code}`);
  }
  // Nor can an individual write the ledger or a wallet directly (the App has no such path either).
  const direct = await clientAs(S.users.c1).rpc("grant_signup_credit", { p_user: S.users.c1 });
  assert.ok(direct.error !== null, "infrx functions are not exposed");
});

test("grant: five concurrent callback/onboarding claims for one verified individual make exactly one +10,000", { skip }, async () => {
  assert.equal((await contextOf(S.users.fresh)).state, "onboarding");
  const rows = await Promise.all(Array.from({ length: 5 }, () => claim(S.users.fresh)));
  assert.equal(rows.filter((row) => row.status === "granted").length, 1);
  assert.equal(rows.filter((row) => row.status === "replayed").length, 4);
  for (const row of rows) assert.equal(row.amount, "10000.00000000");
  // Committed state, re-read: the context is now ready and the balance is the exact string.
  assert.equal((await contextOf(S.users.fresh)).state, "ready");
  const { data, error } = await clientAs(S.users.fresh).rpc("console_wallet_summary", { p_user: S.users.fresh });
  assert.equal(error, null);
  assert.equal((data as { ledger_total: string }[])[0].ledger_total, "10000.00000000");
  assert.equal((await claim(S.users.fresh)).status, "replayed", "a later login");
});

test("grant: a claim for an unverified user is refused by the DB, no wallet", { skip }, async () => {
  assert.equal((await claim(S.users.unverified)).status, "unverified");
  assert.equal((await contextOf(S.users.unverified)).state, "onboarding", "still no wallet");
  assert.equal((await contextOf(S.users.unverified, false)).state, "unverified");
});

test("key create: minted into the individual's own org as a consumer key; the DB stores only the hash", { skip }, async () => {
  const context = await contextOf(S.users.c1);
  assert.equal(context.state, "ready");
  const created = valueOf(await createConsumerActions().createKey(context, storeAs(S.users.c1), { name: "real", idempotency_key: "c3a-real" }), "create");
  const row = await keyRow(S.users.c1, created.id);
  assert.ok(row !== undefined);
  assert.equal(row.org_id, S.orgs.c1);
  assert.equal(row.audience, "consumer");
  assert.equal(row.user_id, S.users.c1, "0009's guard binds the consumer key to its creator");
  assert.equal(row.key_hash, createHash("sha256").update(created.secret ?? "").digest("hex"));
});

test("key create: a context pointing at another org is refused by RLS (the DB backs the action)", { skip }, async () => {
  const context = await contextOf(S.users.c1);
  assert.equal(context.state, "ready");
  if (context.state !== "ready") return;
  for (const org of [S.orgs.c2, S.orgs.shared]) {
    const forged: ConsumerContext = { state: "ready", account: { ...context.account, orgId: org } };
    assert.equal(codeOf(await createConsumerActions().createKey(forged, storeAs(S.users.c1), { name: "x" })), "forbidden", org);
  }
});

test("key revoke: own key once and idempotently; another tenant's or an operator key is not_found", { skip }, async () => {
  const c1 = await contextOf(S.users.c1);
  const act = createConsumerActions();
  const created = valueOf(await act.createKey(c1, storeAs(S.users.c1), { name: "to-revoke" }), "create");
  const first = valueOf(await act.revokeKey(c1, storeAs(S.users.c1), created.id), "revoke");
  assert.ok(first.revoked_at !== null);
  const second = valueOf(await act.revokeKey(c1, storeAs(S.users.c1), created.id), "revoke again");
  assert.equal(second.revoked_at, first.revoked_at);
  // C1 cannot revoke C2's key; C2 (suspended) can revoke its own, not its org's operator key.
  assert.equal(codeOf(await act.revokeKey(c1, storeAs(S.users.c1), S.keys.c2)), "not_found");
  assert.equal((await keyRow(S.users.c2, S.keys.c2))?.revoked_at, null);
  const c2 = await contextOf(S.users.c2);
  assert.equal(c2.state === "ready" && c2.account.suspended, true);
  assert.equal(codeOf(await act.createKey(c2, storeAs(S.users.c2), { name: "new work" })), "org_suspended");
  assert.equal(codeOf(await act.revokeKey(c2, storeAs(S.users.c2), S.keys.c2_operator)), "not_found");
  assert.equal((await keyRow(S.users.c2, S.keys.c2_operator))?.revoked_at, null, "the operator key is untouched");
  valueOf(await act.revokeKey(c2, storeAs(S.users.c2), S.keys.c2), "suspended revoke (R33)");
});

test("key revoke: a member who is not the owner of a shared org cannot revoke there", { skip }, async () => {
  const c2 = await contextOf(S.users.c2);
  if (c2.state !== "ready") assert.fail("c2 is ready");
  const { data } = await clientAs(S.users.shared).from("api_keys").insert({
    org_id: S.orgs.shared, created_by: S.users.shared, name: "shared-owner", prefix: "sk-infrx-sharedAA", key_hash: "c3a-shared-hash",
  }).select("id");
  const id = (data as { id: string }[])[0].id;
  const forged: ConsumerContext = { state: "ready", account: { ...c2.account, orgId: S.orgs.shared } };
  assert.equal(codeOf(await createConsumerActions().revokeKey(forged, storeAs(S.users.c2), id)), "not_found");
  assert.equal((await keyRow(S.users.shared, id))?.revoked_at, null);
});

test("fails-before: the old page action's unconditional re-revoke is a DB error, the adapter's is not", { skip }, async () => {
  const c1 = await contextOf(S.users.c1);
  const act = createConsumerActions();
  const created = valueOf(await act.createKey(c1, storeAs(S.users.c1), { name: "old-path" }), "create");
  valueOf(await act.revokeKey(c1, storeAs(S.users.c1), created.id), "revoke");
  // `app/(console)/api-keys/actions.ts` as it stands: update ... set revoked_at = now() where id and org.
  const old = await clientAs(S.users.c1)
    .from("api_keys")
    .update({ revoked_at: new Date().toISOString() })
    .eq("id", created.id)
    .eq("org_id", S.orgs.c1);
  assert.equal(old.error?.code, "23514", "0009's one-way revocation guard refuses the old path's second click");
  assert.ok(String(old.error?.message).includes(created.id), "and its message (which the old action returned verbatim) names the key id");
  valueOf(await act.revokeKey(c1, storeAs(S.users.c1), created.id), "the adapter's second click answers the first revocation");
});

test("WR-C3A-4: an unverified individual's direct key insert is refused by the table policy (0024)", { skip }, async () => {
  const { error } = await clientAs(S.users.unverified).from("api_keys").insert({
    org_id: S.orgs.unverified, created_by: S.users.unverified, name: "direct", prefix: "sk-infrx-unverif0", key_hash: "c3a-unverified-hash",
  });
  // The action refuses (tests/c/actions.test.ts), and so does the table: 0024's api_keys_insert_owner
  // requires public.consumer_may_create_key() (a verified individual) besides 0001's owner check.
  assert.equal(error?.code, "42501", `direct insert by an unverified individual: ${error === null ? "ACCEPTED" : error.code}`);
  const context = await contextOf(S.users.unverified, false);
  assert.equal(codeOf(await createConsumerActions().createKey(context, storeAs(S.users.unverified), { name: "x" })), "forbidden");
});
