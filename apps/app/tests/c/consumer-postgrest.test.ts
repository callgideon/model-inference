// node --test "tests/**/*.test.ts"   (skips visibly without the stack)
//
// C0 CONSOLE-TENANT through the REAL browser path: the pinned Supabase PostgreSQL with every
// migration, PostgREST v13.0.4, supabase-js, signed JWTs - the composition `lib/services/server.ts`
// builds (`postgrestPort` + `createConsumerReads` over one client), with no service key.
//
//     cd apps/infrx-api && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase \
//         uv run --frozen python ../app/tests/c/realdb/stack.py
//
// The stack script seeds the world (tests/c/realdb/stack.py) and runs this file with
// INFRX_C0_STACK pointing at its manifest. Without it every case SKIPS; it never passes vacuously.

import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import type { Page, Result } from "../../lib/contracts/types.ts";
import { parseCredit, totalCredit, type Credit } from "../../lib/contracts/v2/types.ts";
import { postgrestPort, type PostgrestClient } from "../../lib/services/query.ts";
import {
  consoleShell,
  consumerSessionFrom,
  createConsumerReads,
  resolveConsumerContext,
  type ConsumerAccount,
  type ConsumerClient,
  type ConsumerReads,
  type CreditLedgerEntry,
  type ConsumerRequest,
} from "../../lib/services/console.ts";
import { balanceOutcome, sidebarBalance, sidebarCredit } from "../../lib/services/credits.ts";

type Wallet = { wallet_id: string; ledger_total: string; reserved_total: string; available: string };
type Stack = {
  url: string;
  jwt_secret: string;
  users: Record<"c1" | "c2" | "ungranted" | "shared" | "provider" | "operator" | "empty" | "large", string>;
  orgs: Record<"c1" | "c2" | "shared", string>;
  wallets: Record<"c1" | "c2" | "empty" | "large", Wallet>;
  requests: { short: string; long: string; held: string[]; c2: string };
  keys: Record<"c1" | "c1_revoked" | "c2" | "c2_operator" | "shared" | "stray", string>;
  large_rows: number;
};

const manifest = process.env.INFRX_C0_STACK;
const stack: Stack | null = manifest ? (JSON.parse(readFileSync(manifest, "utf8")) as Stack) : null;
const skip = stack === null ? "needs the task-local stack: run tests/c/realdb/stack.py" : false;
const S = stack as Stack;
const SECRET = "c0-real-cursor-secret-0123456789";
const PLACEHOLDER = "http://c0-stack.invalid";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = encode({ alg: "HS256", typ: "JWT" });
  const body = encode({ ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  const signature = createHmac("sha256", S.jwt_secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${signature}`;
}

type Client = PostgrestClient & { plans: unknown[]; control: { planning: boolean } };

/** A supabase-js client as one browser session (`null` = anonymous) against the task-local PostgREST. */
function clientFor(sub: string | null): Client {
  const token = sub === null ? jwt({ role: "anon" }) : jwt({ role: "authenticated", sub });
  const plans: unknown[] = [];
  const control = { planning: false };
  const client = createClient(PLACEHOLDER, token, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: {
      fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input).replace(`${PLACEHOLDER}/rest/v1`, S.url);
        const headers = new Headers(init?.headers);
        headers.set("Authorization", `Bearer ${token}`);
        if (control.planning && (init?.method ?? "GET") === "GET") {
          // The adapter's OWN request, re-asked for its plan (PGRST_DB_PLAN_ENABLED on this stack).
          const planHeaders = new Headers(headers);
          planHeaders.set("Accept", 'application/vnd.pgrst.plan+json; for="application/json"; options=analyze');
          plans.push(await (await fetch(url, { ...init, headers: planHeaders })).json());
        }
        return fetch(url, { ...init, headers });
      },
    },
  });
  return Object.assign(client as unknown as PostgrestClient, { plans, control });
}

const verifiedUser = (id: string) => ({ id, email: `${id.slice(-4)}@example.com`, email_confirmed_at: "2026-09-01T00:00:00Z" });

async function accountOf(user: string): Promise<ConsumerAccount> {
  const context = await resolveConsumerContext(postgrestPort(clientFor(user)), verifiedUser(user));
  if (context.state !== "ready") assert.fail(`expected a ready account for ${user}, got ${context.state}`);
  return context.account;
}

function readsAs(sub: string | null, account: ConsumerAccount): { reads: ConsumerReads; client: Client } {
  const client = clientFor(sub);
  return { client, reads: createConsumerReads({ pg: postgrestPort(client), rpc: client, cursorSecret: SECRET }, account) };
}

function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}

async function walk<T>(read: (cursor: string | null) => Promise<Result<Page<T>>>, what: string, maxPages = 1000): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | null = null;
  for (let pages = 0; pages < maxPages; pages += 1) {
    const page: Page<T> = valueOf(await read(cursor), what);
    out.push(...page.items);
    cursor = page.next_cursor;
    if (cursor === null) return out;
  }
  return assert.fail(`${what}: more than ${maxPages} pages`);
}

// ---------------------------------------------------------------------------------------- context

test("each session resolves to its own consumer account or a typed state - never another's", { skip }, async () => {
  const c1 = await accountOf(S.users.c1);
  assert.deepEqual(c1, {
    userId: S.users.c1,
    email: `${S.users.c1.slice(-4)}@example.com`,
    walletId: S.wallets.c1.wallet_id,
    orgId: S.orgs.c1,
    suspended: false,
  });
  // CONSUMER_2 belongs to two organizations (their own and SHARED's): the account is the one their
  // wallet funds. Their personal org is suspended (R33): still the account, marked suspended.
  const c2 = await accountOf(S.users.c2);
  assert.deepEqual([c2.orgId, c2.walletId, c2.suspended], [S.orgs.c2, S.wallets.c2.wallet_id, true]);

  for (const [who, user] of [
    ["an individual whose grant is not issued", S.users.ungranted],
    ["a provider developer (provider membership is not a consumer account)", S.users.provider],
    ["an operator (the view shows them EVERY wallet; none is theirs)", S.users.operator],
  ] as const) {
    const context = await resolveConsumerContext(postgrestPort(clientFor(user)), verifiedUser(user));
    assert.deepEqual(context, { state: "onboarding", userId: user, email: `${user.slice(-4)}@example.com` }, who);
  }
  const anonymous = await resolveConsumerContext(postgrestPort(clientFor(null)), verifiedUser(S.users.c1));
  assert.deepEqual(anonymous, { state: "unavailable" }, "an anonymous token cannot read a wallet: unavailable, not onboarding");
});

test("the composed session and shell: an operator without a wallet reaches /admin; an individual is ready", { skip }, async () => {
  // consumerSession()'s own composition over this stack; only GoTrue (absent here) is stubbed.
  const sessionOf = (user: string) => {
    // Delegation, not Object.assign: supabase-js reads its token through its own `auth` on every request.
    const inner = clientFor(user);
    const client = {
      from: (relation: string) => inner.from(relation),
      rpc: (fn: string, args: Parameters<Client["rpc"]>[1]) => inner.rpc(fn, args),
      auth: { getUser: async () => ({ data: { user: verifiedUser(user) }, error: null }) },
    } as unknown as ConsumerClient;
    return consumerSessionFrom(async () => client, () => SECRET);
  };
  const routes = { verifyEmail: "/verify-email", onboarding: "/onboarding" };
  const operator = await sessionOf(S.users.operator);
  assert.equal(operator.context.state, "onboarding", "the view shows the operator every wallet; none is theirs");
  assert.deepEqual(consoleShell(operator, true, routes), { kind: "render", email: `${S.users.operator.slice(-4)}@example.com`, reads: null });
  assert.deepEqual(consoleShell(await sessionOf(S.users.ungranted), false, routes), { kind: "redirect", to: "/onboarding" });
  const c1 = await sessionOf(S.users.c1);
  const shell = consoleShell(c1, false, routes);
  assert.equal(shell.kind, "render");
  const balance = valueOf(await (shell as { reads: ConsumerReads }).reads.balance(), "c1 balance through the composed session");
  assert.equal(balance.available, S.wallets.c1.available);
});

test("fails-before: the legacy session picks the first of several memberships", { skip }, async () => {
  // lib/session.ts reads `org_members ... .limit(1)`: for CONSUMER_2 that is one of TWO rows, chosen by
  // no rule about ownership. The C0 context above resolves the wallet's own personal org instead.
  const { data, error } = await (clientFor(S.users.c2) as unknown as {
    from(r: string): { select(c: string): { eq(c: string, v: string): Promise<{ data: { org_id: string }[] | null; error: unknown }> } };
  })
    .from("org_members")
    .select("org_id")
    .eq("user_id", S.users.c2);
  assert.equal(error, null);
  assert.deepEqual(new Set((data ?? []).map((row) => row.org_id)), new Set([S.orgs.c2, S.orgs.shared]));
});

// ---------------------------------------------------------------------------------------- balance

test("the CREDIT balance is the durable wallet's, exact, with every hold counted", { skip }, async () => {
  for (const name of ["c1", "c2", "empty", "large"] as const) {
    const user = S.users[name];
    const { reads } = readsAs(user, await accountOf(user));
    const balance = valueOf(await reads.balance(), `${name} balance`);
    const want = S.wallets[name];
    assert.deepEqual(
      [balance.wallet_id, balance.unit, balance.ledger_total, balance.reserved_total, balance.available],
      [want.wallet_id, "CREDIT", want.ledger_total, want.reserved_total, want.available],
      name,
    );
  }
  const empty = valueOf(await readsAs(S.users.empty, await accountOf(S.users.empty)).reads.balance(), "empty");
  assert.equal(empty.ledger_total, "10000.00000000", "the one-time grant, exactly");
});

test("fails-before: the legacy sidebar shows USD org history, not the individual's CREDIT", { skip }, async (t) => {
  const account = await accountOf(S.users.c1);
  const { client, reads } = readsAs(S.users.c1, account);
  // lib/credits.ts `getBalance(session.orgId)`: org_wallet_summary, legacy USD.
  const legacy = await client.rpc("org_wallet_summary", { p_org: S.orgs.c1 });
  const old = sidebarBalance(balanceOutcome((legacy.data as never[] | null)?.[0] ?? (legacy.data as never), legacy.error));
  const now = sidebarCredit(await reads.balance());
  assert.ok(now !== null && now.endsWith(" credits"), `the C0 sidebar shows credits: ${now}`);
  assert.ok(old === null || !old.includes("credits"), `the legacy sidebar is not the CREDIT balance: ${old}`);
  assert.notEqual(old, now);
  t.diagnostic(`legacy sidebar: ${old}; C0 sidebar: ${now}`);
});

test("another session, or no session, cannot read an account by naming it", { skip }, async () => {
  const c1 = await accountOf(S.users.c1);
  for (const [who, sub] of [
    ["CONSUMER_2", S.users.c2],
    ["anonymous", null],
  ] as const) {
    const { reads } = readsAs(sub, c1);
    assert.equal((await reads.balance()).ok, false, `${who}: balance`);
    assert.equal((await reads.legacyUsd()).ok, false, `${who}: legacy statement`);
    const ledger = await reads.ledger({});
    assert.ok(!ledger.ok || ledger.value.items.length === 0, `${who}: no CREDIT ledger row of C1`);
    const keys = await reads.keys();
    assert.ok(!keys.ok || keys.value.length === 0, `${who}: no key of C1`);
    const result = await reads.result(S.requests.long);
    // anon holds no EXECUTE on the consumer functions (42501); another individual gets not_found.
    assert.equal(!result.ok && result.error.code, sub === null ? "forbidden" : "not_found", `${who}: result`);
    const detail = await reads.request(S.requests.long);
    assert.equal(detail.ok, false, `${who}: request detail`);
    const requests = await reads.requests({});
    if (requests.ok) {
      assert.ok(!requests.value.items.some((row) => row.request_id === S.requests.long), `${who}: C1's requests`);
    }
  }
});

// ---------------------------------------------------------------------------------- legacy USD

test("legacy USD is a separate, exact statement; an individual without history has none", { skip }, async () => {
  const c1 = valueOf(await readsAs(S.users.c1, await accountOf(S.users.c1)).reads.legacyUsd(), "c1 legacy");
  assert.deepEqual(c1 && [c1.balance, c1.entry_count, c1.rollout_hold, c1.org_id], ["12.34567891", 1, true, S.orgs.c1]);
  const empty = valueOf(await readsAs(S.users.empty, await accountOf(S.users.empty)).reads.legacyUsd(), "empty legacy");
  assert.equal(empty, null);
});

// -------------------------------------------------------------------------------------- ledger

test("the CREDIT ledger walks its own wallet exactly once and sums to the durable total", { skip }, async () => {
  const account = await accountOf(S.users.c1);
  const { reads } = readsAs(S.users.c1, account);
  for (const limit of [1, 2, 100]) {
    const entries = await walk<CreditLedgerEntry>((cursor) => reads.ledger({ limit, cursor }), `c1 ledger/${limit}`);
    assert.deepEqual(entries.map((e) => e.kind).sort(), ["inference_debit", "inference_debit", "signup_grant"]);
    assert.equal(new Set(entries.map((e) => e.entry_id)).size, entries.length, "no entry twice");
    assert.equal(totalCredit(entries.map((e) => e.amount)), parseCredit(S.wallets.c1.ledger_total), "ledger = wallet total");
    const debited = new Set(entries.flatMap((e) => (e.request_id === null ? [] : [e.request_id])));
    assert.deepEqual(debited, new Set([S.requests.short, S.requests.long]), "one debit per settled request");
  }
});

test("a large history pages on the index, every entry once, to the exact durable total", { skip }, async (t) => {
  const account = await accountOf(S.users.large);
  const { reads, client } = readsAs(S.users.large, account);
  const seen = new Set<string>();
  const amounts: Credit[] = [];
  let cursor: string | null = null;
  let pages = 0;
  do {
    client.control.planning = pages === 100; // one mid-walk page's plan, keyset bound included
    const page: Page<CreditLedgerEntry> = valueOf(await reads.ledger({ limit: 100, cursor }), `large page ${pages}`);
    for (const entry of page.items) {
      assert.ok(!seen.has(entry.entry_id), `entry ${entry.entry_id} twice (ties on created_at)`);
      seen.add(entry.entry_id);
      amounts.push(entry.amount);
    }
    cursor = page.next_cursor;
    pages += 1;
  } while (cursor !== null && pages < 1000);
  assert.equal(seen.size, S.large_rows, "every entry, over 500 tied instants");
  assert.equal(totalCredit(amounts), parseCredit(S.wallets.large.ledger_total));
  const plan = JSON.stringify(client.plans);
  assert.equal(client.plans.length, 1);
  assert.match(plan, /credit_ledger_wallet_created_idx/, `the page must use the wallet index: ${plan.slice(0, 400)}`);
  assert.doesNotMatch(plan, /"Seq Scan"[^}]*"Relation Name":"credit_ledger"/, "no full ledger scan per page");
  // Coarse regression bound: with the view's per-row `visible_principal()` selected this page took
  // 1.4 s on this fixture (measured); without it, milliseconds.
  const executed = (client.plans[0] as { "Execution Time"?: number }[])[0]?.["Execution Time"];
  assert.ok(typeof executed === "number" && executed < 250, `one page executes in bounded time: ${executed} ms`);
  t.diagnostic(`${pages} pages of 100; page 100 executed in ${executed} ms`);
});

// ------------------------------------------------------------------------------------ requests

test("requests: own rows only, each once across tied timestamps, charge in its own unit", { skip }, async () => {
  const { reads } = readsAs(S.users.c1, await accountOf(S.users.c1));
  const all = [S.requests.short, S.requests.long, ...S.requests.held];
  for (const limit of [1, 2, 5, 100]) {
    const rows = await walk<ConsumerRequest>((cursor) => reads.requests({ limit, cursor }), `c1 requests/${limit}`);
    assert.deepEqual(rows.map((r) => r.request_id).sort(), [...all].sort(), `limit ${limit}`);
  }
  const byId = new Map((await walk<ConsumerRequest>((cursor) => reads.requests({ limit: 50, cursor }), "c1")).map((r) => [r.request_id, r]));
  const long = byId.get(S.requests.long)!;
  const short = byId.get(S.requests.short)!;
  assert.deepEqual([long.unit, long.result, short.result], ["CREDIT", "available", "expired"], "the persisted expiry decides");
  assert.ok(long.charged !== null && parseCredit(long.charged) === long.charged, "a settled CREDIT charge is exact");
  for (const id of S.requests.held) {
    const held = byId.get(id)!;
    assert.deepEqual([held.charged, held.result], [null, "pending"], "an unsettled request has no charge, not zero");
    assert.ok(held.hold !== null, "and shows its active hold");
  }
  const c2 = await walk<ConsumerRequest>(
    async (cursor) => readsAs(S.users.c2, await accountOf(S.users.c2)).reads.requests({ cursor }),
    "c2 requests",
  );
  assert.deepEqual(c2.map((r) => r.request_id), [S.requests.c2]);
  const empty = valueOf(await readsAs(S.users.empty, await accountOf(S.users.empty)).reads.requests({}), "empty");
  assert.deepEqual(empty, { items: [], next_cursor: null });
});

test("request detail and result: owned and unexpired only, typed refusals otherwise", { skip }, async () => {
  const { reads } = readsAs(S.users.c1, await accountOf(S.users.c1));
  const detail = valueOf(await reads.request(S.requests.long), "long detail");
  assert.equal(detail.request_id, S.requests.long);
  assert.equal(valueOf(await reads.result(S.requests.long), "long result"), `result of ${S.requests.long}`);
  const expired = await reads.result(S.requests.short);
  assert.equal(!expired.ok && expired.error.code, "result_expired");
  const held = await reads.result(S.requests.held[0]);
  assert.ok(!held.ok && ["not_found", "result_pending"].includes(held.error.code), "a pending request has no result");
  const foreign = await reads.request(S.requests.c2);
  assert.equal(!foreign.ok && foreign.error.code, "not_found", "another individual's request id");
  const guessed = await reads.result("5c000000-0000-4000-8000-00000000ffff");
  assert.equal(!guessed.ok && guessed.error.code, "not_found");
});

// ---------------------------------------------------------------------------------------- keys

test("keys: the personal organization's key metadata, not another org the individual can see", { skip }, async () => {
  const c1 = valueOf(await readsAs(S.users.c1, await accountOf(S.users.c1)).reads.keys(), "c1 keys");
  assert.deepEqual(new Set(c1.map((k) => k.id)), new Set([S.keys.c1, S.keys.c1_revoked]), "not the stray key in ORG_A");
  assert.ok(c1.find((k) => k.id === S.keys.c1_revoked)?.revoked_at !== null, "a revoked key says so");
  assert.ok(c1.every((k) => !("key_hash" in k)));
  // RLS lets CONSUMER_2 read SHARED's keys (a member); the account's keys are their own org's only.
  const c2 = valueOf(await readsAs(S.users.c2, await accountOf(S.users.c2)).reads.keys(), "c2 keys");
  assert.deepEqual(new Set(c2.map((k) => k.id)), new Set([S.keys.c2, S.keys.c2_operator]));
});
