// node --test "tests/**/*.test.ts"   (skips visibly without a database)
//
// A2 against REAL PostgreSQL: the exact calls the App makes — `claim_signup_grant` as the
// service role (what supabase-js `.rpc()` sends through PostgREST, named arguments) and
// `console_wallet_summary` as the signed-in user — on every migration, through the same pure
// mappers the pages use. The DB rules themselves (R71 races, identity reuse) are A1's own
// tests; these prove the App's call shape, its role boundary and its reading of the answers.
//
//   cd apps/infrx-api && INFRX_D_TASK=app-a2 uv run --frozen python ../app/tests/a/pg_up.py &
//   cd apps/app && INFRX_APP_A2_DSN=<the DSN pg_up prints> node --test tests/a/grant-pg.test.ts
//
// Port 55460 (`app-a2` in contracts/tasklocal.py); never the shared 55432, never hosted.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import test from "node:test";

import { SIGNUP_CAMPAIGN, claimOutcome, walletBalance } from "../../app/(auth)/flow.ts";

const DSN = process.env.INFRX_APP_A2_DSN;
const skip = DSN ? false : "not run: INFRX_APP_A2_DSN is not set (start tests/a/pg_up.py)";
const run = promisify(execFile);

async function sql(statement: string): Promise<string> {
  const { stdout } = await run("psql", ["-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", DSN!, "-c", statement], {
    encoding: "utf8",
  });
  return stdout.trim();
}

/** The SQLSTATE a statement fails with, or null when it succeeds. */
async function sqlstate(statement: string): Promise<string | null> {
  try {
    await run("psql", ["-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-d", DSN!, "-c", statement], {
      encoding: "utf8",
    });
    return null;
  } catch (failure) {
    const text = String((failure as { stderr?: string }).stderr ?? failure);
    return /ERROR:\s+([0-9A-Z]{5}):/.exec(text)?.[1] ?? text;
  }
}

/** One psql session, as PostgREST runs a service-role rpc: SET ROLE, then the function by name. */
const asService = (body: string) => `begin; set local role service_role; ${body}; commit;`;
const asUser = (user: string, body: string) =>
  `begin; set local role authenticated; set local request.jwt.claims = '{"sub":"${user}","role":"authenticated"}'; ${body}; commit;`;

/** `supabase.rpc("claim_signup_grant", {p_user_id, p_campaign_version})` as JSON rows. */
async function claim(user: string) {
  const out = await sql(
    asService(
      `select coalesce(json_agg(t), '[]') from public.claim_signup_grant(p_user_id => '${user}', p_campaign_version => '${SIGNUP_CAMPAIGN}') t`,
    ),
  );
  return JSON.parse(out) as Record<string, unknown>[];
}

async function wallet(user: string) {
  const out = await sql(asUser(user, `select row_to_json(t) from public.console_wallet_summary(p_user => '${user}') t`));
  return JSON.parse(out) as Record<string, unknown>;
}

/** A fresh individual through the real signup trigger (0001): profile, personal org, owner row. */
async function newUser(verified: boolean): Promise<string> {
  const id = randomUUID();
  await sql(
    `insert into auth.users (id, email, email_confirmed_at) values ('${id}', 'a2-${id}@example.test', ${verified ? "now()" : "null"})`,
  );
  return id;
}

const ledgerRows = (user: string) =>
  sql(
    `select count(*) from infrx.credit_ledger l join infrx.credit_wallets w using (wallet_id) where w.owner_user_id = '${user}' and l.kind = 'signup_grant'`,
  );

test.before(async () => {
  if (skip) return;
  // The grant ships OFF (0006); the lane's own throwaway database turns it on.
  await sql(`update infrx.feature_flags set enabled = true, updated_by = 'a2-test', reason = 'A2 local test' where name = 'signup_grant'`);
});

test("A2-PG-01 unverified: no grant, no wallet, and the page reads not_issued — then verification grants exactly 10,000", { skip }, async () => {
  const user = await newUser(false);
  assert.deepEqual(claimOutcome(await claim(user), null), { kind: "unverified" });
  assert.deepEqual(walletBalance(await wallet(user), null), { kind: "not_issued" }, "no wallet is not a confirmed zero");
  assert.equal(await ledgerRows(user), "0");

  await sql(`update auth.users set email_confirmed_at = now() where id = '${user}'`);
  const granted = claimOutcome(await claim(user), null);
  assert.equal(granted.kind, "credited");
  assert.equal(granted.kind === "credited" && granted.first, true);
  assert.equal(granted.kind === "credited" && granted.amount, "10000.00000000");
  const balance = walletBalance(await wallet(user), null);
  assert.equal(balance.kind, "available");
  assert.equal(balance.kind === "available" && balance.available, "10000.00000000");
});

test("A2-PG-02 duplicate callbacks, sign-ins and retries racing: one ledger entry, one `first`, the rest replays", { skip }, async () => {
  const user = await newUser(true);
  const answers = await Promise.all(Array.from({ length: 8 }, () => claim(user)));
  const outcomes = answers.map((rows) => claimOutcome(rows, null));
  assert.ok(outcomes.every((o) => o.kind === "credited"), JSON.stringify(outcomes));
  assert.equal(outcomes.filter((o) => o.kind === "credited" && o.first).length, 1, "exactly one caller sees the first grant");
  assert.equal(await ledgerRows(user), "1");
  assert.equal((walletBalance(await wallet(user), null) as { available?: string }).available, "10000.00000000");
});

test("A2-PG-03 a new organization membership and a later claim never multiply the grant", { skip }, async () => {
  const user = await newUser(true);
  assert.equal(claimOutcome(await claim(user), null).kind, "credited");
  const owner = await newUser(true);
  const org = await sql(`select id from public.organizations where created_by = '${owner}'`);
  await sql(`insert into public.org_members (org_id, user_id, role) values ('${org}', '${user}', 'member')`);
  const again = claimOutcome(await claim(user), null);
  assert.equal(again.kind === "credited" && again.first, false, "a replay, not a second grant");
  assert.equal(await ledgerRows(user), "1");
});

test("A2-PG-04 the browser roles cannot call the grant, and a user cannot read another user's wallet", { skip }, async () => {
  const user = await newUser(true);
  const other = await newUser(true);
  const call = `select * from public.claim_signup_grant(p_user_id => '${user}', p_campaign_version => 'x')`;
  assert.equal(await sqlstate(`begin; set local role anon; ${call}; commit;`), "42501", "anon");
  assert.equal(await sqlstate(asUser(user, call)), "42501", "a signed-in user calling the action directly");
  assert.equal(await ledgerRows(user), "0", "a refused direct call minted nothing");
  assert.equal(
    await sqlstate(asUser(user, `select * from public.console_wallet_summary(p_user => '${other}')`)),
    "42501",
    "another user's wallet",
  );
});

test("A2-PG-05 grant switched off reads as unavailable (retryable), never credited", { skip }, async () => {
  const user = await newUser(true);
  await sql(`update infrx.feature_flags set enabled = false where name = 'signup_grant'`);
  try {
    const state = await sqlstate(
      asService(`select * from public.claim_signup_grant(p_user_id => '${user}', p_campaign_version => '${SIGNUP_CAMPAIGN}')`),
    );
    assert.equal(state, "55000");
    assert.deepEqual(claimOutcome(null, { code: state, message: "maintenance" }), { kind: "unavailable" });
    assert.deepEqual(walletBalance(await wallet(user), null), { kind: "not_issued" });
  } finally {
    await sql(`update infrx.feature_flags set enabled = true where name = 'signup_grant'`);
  }
});
