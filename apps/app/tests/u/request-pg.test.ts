// node --test "tests/**/*.test.ts"  — skipped unless run by tests/u/request_world.py
//
// U4 on real PostgreSQL (USER-RESULTS, RESULT-EXPIRY, CONSOLE-TENANT). request_world.py built
// requests through the real admission/claim/terminalize functions — two results settled under
// different result-TTL configurations, an unknown-usage success, a platform failure, a running and
// a queued request, and another individual's result — and recorded what the API's own owned-result
// read (`get_owned_credit` + F2C.b `read_outcome`) answers for each at three store-clock instants.
//
// The App's unchanged `postgrestRequestReads` runs against a PostgREST double: each RPC becomes the
// SQL PostgREST issues, executed by `psql` as the real browser principal (role `authenticated` with
// a JWT subject; `anon` without one). Every answer is compared with the API's and with durable rows.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

import { credits } from "../../lib/format.ts";
import type { Answer, CreditClient } from "../../app/(console)/billing/credit-reads.ts";
import { postgrestRequestReads, resultResponse } from "../../app/(console)/usage/[requestId]/request-reads.ts";
import { requestDetailModel, watchExpiry, type RequestDetail } from "../../app/(console)/usage/[requestId]/request-view-model.ts";

const DSN = process.env.U4_PG_DSN;
const skip = DSN === undefined ? "U4_PG_DSN is not set: run tests/u/request_world.py (task-local PostgreSQL)" : false;
type World = {
  user: string;
  other: string;
  jobs: Record<string, string>;
  api: Record<string, Record<string, string>>;
  base_offset: number;
  instants: number[];
  kept_ttl: number;
  short_ttl: number;
  provider: string;
  ungranted: string;
  org_a: string;
};
const WORLD: World = DSN === undefined ? ({ jobs: {}, api: {} } as unknown as World) : JSON.parse(process.env.U4_PG_WORLD ?? "{}");
const MINE = ["kept", "short", "unknown", "failed", "running", "queued"];

/** Connection by environment, never by argv: the DSN carries the local test password. */
function pgEnv(): NodeJS.ProcessEnv {
  const url = new URL(DSN as string);
  return {
    ...process.env,
    PGHOST: url.hostname,
    PGPORT: url.port,
    PGUSER: decodeURIComponent(url.username),
    PGPASSWORD: decodeURIComponent(url.password),
    PGDATABASE: url.pathname.slice(1),
    PGCONNECT_TIMEOUT: "10",
  };
}

type Principal = { user: string | null; prelude?: string } | "owner";

/**
 * One statement in a rolled-back transaction: as the owner, or as the browser principal PostgREST
 * would switch to — `authenticated` with a JWT subject, or `anon` with none. `prelude` runs as the
 * owner first, in the same transaction (a membership change the principal then reads under).
 */
function psql(who: Principal, statement: string) {
  let impersonate = "";
  if (who !== "owner") {
    const role = who.user === null ? "anon" : "authenticated";
    const claims = who.user === null ? `{"role":"anon"}` : `{"sub":"${who.user}","role":"authenticated"}`;
    impersonate =
      `${who.prelude ?? ""}\n\\o /dev/null\nset local role ${role};\n` +
      `select set_config('request.jwt.claims', '${claims}', true), set_config('request.jwt.claim.sub', '${who.user ?? ""}', true), set_config('request.jwt.claim.role', '${role}', true);\n\\o\n`;
  }
  const script = `\\set ON_ERROR_STOP 1\n\\set VERBOSITY verbose\nbegin;\n${impersonate}${statement};\nrollback;\n`;
  return spawnSync("psql", ["-X", "-q", "-A", "-t"], { input: script, env: pgEnv(), encoding: "utf8" });
}

function sql(who: Principal, statement: string): Answer {
  const run = psql(who, statement);
  if (run.status !== 0) {
    const match = /ERROR:\s+([0-9A-Z]{5}):\s*(.*)/.exec(run.stderr);
    if (match === null) throw new Error(`psql failed: ${run.stderr.slice(0, 300)}`);
    return { data: null, error: { code: match[1], message: match[2] } };
  }
  return { data: JSON.parse(run.stdout.trim()), error: null };
}

const IDENT = /^[a-z_]+$/;
function lit(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  if (typeof value === "string") return `'${value.replaceAll("'", "''")}'`;
  throw new TypeError(`no literal for ${JSON.stringify(value)}`);
}
function ident(name: string): string {
  if (!IDENT.test(name)) throw new TypeError(`not an identifier: ${name}`);
  return name;
}

/** PostgREST's RPC rendering: a set-returning function as a JSON array, a scalar one as its value. */
const SCALAR = new Set(["consumer_job_result"]);
function pgClient(who: Principal): CreditClient {
  return {
    from: () => {
      throw new Error("the request reads use only the consumer RPCs");
    },
    rpc: (fn, args) =>
      Promise.resolve().then(() => {
        const call = `public.${ident(fn)}(${Object.entries(args).map(([k, v]) => `${ident(k)} => ${lit(v)}`).join(", ")})`;
        return sql(who, SCALAR.has(fn) ? `select to_json(${call})` : `select coalesce(json_agg(t), '[]'::json) from (select * from ${call}) t`);
      }),
  };
}

const as = (user: string, prelude?: string) => postgrestRequestReads(pgClient({ user, prelude }), user);

function durable<T>(statement: string): T[] {
  const answer = sql("owner", `select coalesce(json_agg(t), '[]'::json) from (${statement}) t`);
  assert.equal(answer.error, null, JSON.stringify(answer.error));
  return answer.data as T[];
}

/** Move the store clock (the frozen test clock) to `delta` seconds after the world was built. */
function at(delta: number) {
  const run = spawnSync("psql", ["-X", "-q", "-A", "-t", "-c", `select infrx_test.set_offset(${WORLD.base_offset + delta})`], { env: pgEnv(), encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
}

async function detail(user: string, id: string): Promise<RequestDetail> {
  const model = requestDetailModel(await as(user).job(id));
  assert.equal(model.kind, "ready", JSON.stringify(model));
  return (model as { value: RequestDetail }).value;
}

/** The API's `read_outcome` and the page's result state, which must say the same thing. */
const RESULT_OF: Record<string, string> = {
  available: "ready",
  expired: "expired",
  pending: "pending",
  held_unknown: "withheld",
  no_result: "no_result",
  unavailable: "unavailable",
};

const T = {
  agree: "U4-P01 at every store-clock instant the page's result access and result read agree with the API's owned-result read",
  content: "U4-P02 an available result is the stored body, exactly, and the charge shown is the durable debit",
  expiry: "U4-P03 the persisted expiry decides: a changed TTL moves only later results, and expiry removes content but keeps metadata and charge",
  open: "U4-P04 a page open across the expiry: its expiry driver drops content at the store instant, a reload reads expired, the route answers without content",
  tenant: "U4-P05 another individual, a provider, an ungranted user, a role change and a guessed link all read 'not found'",
  anon: "U4-P06 without a JWT subject nothing is read: the job read is refused and the result read is signed out",
  gate: "U4-P07 an unknown-usage success is withheld by the App as by the API",
  direct: "U4-P08 consumer_job_result alone refuses what the API withholds: an unknown-usage success is result_pending",
};

test(T.agree, { skip }, async () => {
  try {
    for (const delta of WORLD.instants) {
      at(delta);
      const api = WORLD.api[String(delta)];
      for (const name of MINE) {
        const id = WORLD.jobs[name];
        const page = await detail(WORLD.user, id);
        assert.equal(page.result.access, api[name], `${name} at +${delta}s: page access vs API`);
        const read = await as(WORLD.user).result(id);
        assert.equal(read.state, RESULT_OF[api[name]], `${name} at +${delta}s: result read vs API`);
        assert.equal(page.poll, name === "running" || name === "queued", `${name}: polled`);
      }
      // The other individual's own result follows the same rules in their session.
      const theirs = await as(WORLD.other).result(WORLD.jobs.theirs);
      assert.equal(theirs.state, RESULT_OF[api.theirs], `theirs at +${delta}s`);
    }
    // The instants cover every state the brief names.
    const seen = new Set(Object.values(WORLD.api).flatMap((row) => Object.values(row)));
    for (const state of ["available", "expired", "pending", "held_unknown", "no_result"]) assert.ok(seen.has(state), state);
  } finally {
    at(0);
  }
});

test(T.content, { skip }, async () => {
  at(0);
  for (const name of ["kept", "short"]) {
    const id = WORLD.jobs[name];
    const read = await as(WORLD.user).result(id);
    const [stored] = durable<{ body: string }>(`select body from infrx.job_results where request_id = ${lit(id)}`);
    assert.deepEqual(read, { state: "ready", text: stored.body }, name);
    const [debit] = durable<{ amount: string }>(
      `select (-amount)::text as amount from infrx.credit_ledger where request_id = ${lit(id)} and kind = 'inference_debit'`,
    );
    const page = await detail(WORLD.user, id);
    assert.equal(page.charge.label, "Charged");
    assert.equal(page.charge.amount, credits(debit.amount), `${name}: charge is the durable debit`);
    assert.equal(page.unit, "credits");
  }
  const failed = await detail(WORLD.user, WORLD.jobs.failed);
  assert.equal(failed.charge.amount, null, "an absorbed platform failure shows no charge");
  assert.equal(failed.charge.label, "No charge");
  assert.ok(failed.failure !== null && /our side/.test(failed.failure.title));
  const running = await detail(WORLD.user, WORLD.jobs.running);
  assert.equal(running.charge.amount, null, "a hold is not a charge");
  assert.ok(running.charge.held !== null && running.charge.held.endsWith(" credits"));
});

test(T.expiry, { skip }, async () => {
  try {
    at(0);
    const rows = durable<{ request_id: string; ttl_s: number; expires: string }>(
      `select request_id, extract(epoch from result_expires_at - settled_at)::float8 as ttl_s, to_json(result_expires_at)#>>'{}' as expires from infrx.jobs where request_id in (${lit(WORLD.jobs.kept)}, ${lit(WORLD.jobs.short)})`,
    );
    const ttl = Object.fromEntries(rows.map((r) => [r.request_id, r]));
    assert.equal(ttl[WORLD.jobs.kept].ttl_s, WORLD.kept_ttl, "the first result keeps the TTL it settled under");
    assert.equal(ttl[WORLD.jobs.short].ttl_s, WORLD.short_ttl, "the changed TTL applies to the later result only");
    const before = await detail(WORLD.user, WORLD.jobs.kept);
    assert.equal(Date.parse(before.result.expiresAt as string), Date.parse(ttl[WORLD.jobs.kept].expires), "UI expiry is the persisted one");

    at(WORLD.short_ttl * 2);
    assert.equal((await detail(WORLD.user, WORLD.jobs.short)).result.access, "expired");
    assert.equal((await detail(WORLD.user, WORLD.jobs.kept)).result.access, "available", "a shorter TTL does not cut an earlier promise");

    at(WORLD.kept_ttl + 1);
    const after = await detail(WORLD.user, WORLD.jobs.kept);
    assert.equal(after.result.access, "expired");
    assert.deepEqual(await as(WORLD.user).result(WORLD.jobs.kept), { state: "expired" });
    // Metadata and charge are retained; only content access went.
    for (const key of ["requestId", "created", "model", "revision", "mode", "status", "charge", "tokens"] as const) {
      assert.deepEqual(after[key], before[key], key);
    }
    assert.match(after.result.note, /expired at/);
  } finally {
    at(0);
  }
});

test(T.open, { skip }, async () => {
  try {
    at(0);
    const id = WORLD.jobs.short;
    const open = await detail(WORLD.user, id);
    assert.equal((await as(WORLD.user).result(id)).state, "ready");
    // The open page's expiry driver, run on the store clock with its timer fired by hand.
    const storeNow = () => Date.parse(durable<{ now: string }>(`select to_json(infrx.now())#>>'{}' as now`)[0].now);
    const armed: { ms: number; run: () => void }[] = [];
    let dropped = false;
    watchExpiry(open.result.expiresAt as string, storeNow, (run, ms) => (armed.push({ ms, run }), () => {}), () => (dropped = true));
    assert.deepEqual(armed.map((t) => t.ms), [WORLD.short_ttl * 1000], "the browser timer is armed for the persisted expiry");

    at(WORLD.short_ttl - 1); // the timer fires a second early
    armed.shift()?.run();
    assert.equal(dropped, false, "content is not dropped before the persisted expiry");
    assert.deepEqual(armed.map((t) => t.ms), [1000], "re-armed for the second left");

    at(WORLD.short_ttl); // exactly at the expiry: equality has passed
    armed.shift()?.run();
    assert.equal(dropped, true, "the open page drops the content at the store instant");
    const reload = await as(WORLD.user).result(id);
    assert.deepEqual(reload, { state: "expired" }, "a reload or reconnect reads expired, never the cached body");
    const response = resultResponse(reload);
    assert.equal(response.status, 410);
    assert.equal(response.headers.get("cache-control"), "private, no-store, max-age=0");
    assert.equal("text" in (await response.json()), false);
    assert.equal((await detail(WORLD.user, id)).result.access, "expired");
  } finally {
    at(0);
  }
});

test(T.tenant, { skip }, async () => {
  at(0);
  const [org] = durable<{ id: string }>(`select personal_org_id as id from infrx.credit_wallets where owner_user_id = ${lit(WORLD.user)} and kind = 'consumer'`);
  // Role changes. Joining a funded personal organization is refused by the database itself...
  const join = `insert into public.org_members (org_id, user_id, role) values (${lit(org.id)}, ${lit(WORLD.other)}, 'owner')`;
  const refused = sql({ user: WORLD.other, prelude: `${join};` }, "select 1");
  assert.equal(refused.error?.code, "23514", "a funded personal organization's membership is frozen");
  // ...and a role elsewhere — both individuals owners of one shared organization — reaches nothing.
  const promoted = [WORLD.user, WORLD.other]
    .map((u) => `insert into public.org_members (org_id, user_id, role) values (${lit(WORLD.org_a)}, ${lit(u)}, 'owner') on conflict (org_id, user_id) do update set role = 'owner';`)
    .join("\n");
  const probe = sql({ user: WORLD.other, prelude: promoted }, "select 1");
  assert.equal(probe.error, null, `the role change itself failed: ${JSON.stringify(probe.error)}`);
  const probes: [string, ReturnType<typeof as>][] = [
    ["another individual", as(WORLD.other)],
    ["a provider admin", as(WORLD.provider)],
    ["an ungranted individual", as(WORLD.ungranted)],
    ["another individual made a co-owner of an organization with this one", as(WORLD.other, promoted)],
  ];
  for (const [who, reads] of probes) {
    for (const name of MINE) {
      assert.deepEqual(await reads.job(WORLD.jobs[name]), { ok: true, value: null }, `${who}: ${name}`);
      assert.deepEqual(await reads.result(WORLD.jobs[name]), { state: "not_found" }, `${who}: ${name}`);
    }
  }
  // The other way round, and a guessed id.
  assert.deepEqual(await as(WORLD.user).job(WORLD.jobs.theirs), { ok: true, value: null });
  assert.deepEqual(await as(WORLD.user).result(WORLD.jobs.theirs), { state: "not_found" });
  const guessed = "b1000000-0000-4000-8000-00000000abcd";
  assert.deepEqual(await as(WORLD.user).job(guessed), { ok: true, value: null });
  assert.deepEqual(await as(WORLD.user).result(guessed), { state: "not_found" });
  // Direct content reads of a foreign id refuse too (the App never asks, but the grant is real).
  const direct = sql({ user: WORLD.other }, `select to_json(public.consumer_job_result(${lit(WORLD.jobs.kept)}))`);
  assert.equal(direct.error?.code, "P0001");
  assert.match(direct.error?.message ?? "", /^not_found:/);
  // Their own request stays readable to them, before and after the role change.
  for (const reads of [as(WORLD.other), as(WORLD.other, promoted)]) {
    const own = await reads.job(WORLD.jobs.theirs);
    assert.equal(own.ok && own.value?.requestId, WORLD.jobs.theirs);
  }
});

test(T.anon, { skip }, async () => {
  at(0);
  const anon = postgrestRequestReads(pgClient({ user: null }), "");
  const job = await anon.job(WORLD.jobs.kept);
  assert.equal(job.ok, false, "anon read a job");
  assert.equal(!job.ok && job.error.code, "forbidden");
  assert.deepEqual(await anon.result(WORLD.jobs.kept), { state: "signed_out" });
});

test(T.gate, { skip }, async () => {
  at(0);
  const id = WORLD.jobs.unknown;
  assert.equal(WORLD.api["0"].unknown, "held_unknown");
  assert.deepEqual(await as(WORLD.user).result(id), { state: "withheld" });
  const [row] = durable<{ state: string; settlement_state: string; expires: string | null }>(
    `select state, settlement_state, result_expires_at::text as expires from infrx.jobs where request_id = ${lit(id)}`,
  );
  assert.equal(row.settlement_state, "held_unknown");
  assert.equal(row.state, "succeeded", "the case the RPC must withhold (P08) is a stored success");
  assert.notEqual(row.expires, null);
});

// The grant to `authenticated` is a real PostgREST endpoint: the anon key plus the user's JWT
// reaches it without the App, so the RPC itself must withhold what `read_outcome` withholds.
test(T.direct, { skip }, () => {
  at(0);
  const direct = sql({ user: WORLD.user }, `select to_json(public.consumer_job_result(${lit(WORLD.jobs.unknown)}))`);
  assert.equal(direct.data, null, "consumer_job_result served an unknown-usage result");
  assert.equal(direct.error?.code, "P0001");
  assert.match(direct.error?.message ?? "", /^result_pending:/);
  // Its settled sibling is still served, so the refusal is the usage gate, not a broken RPC.
  const kept = sql({ user: WORLD.user }, `select to_json(public.consumer_job_result(${lit(WORLD.jobs.kept)}))`);
  assert.equal(kept.error, null);
  assert.equal(typeof kept.data, "string");
});
