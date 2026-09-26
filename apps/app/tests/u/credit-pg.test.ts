// node --test "tests/**/*.test.ts"  — skipped unless run by tests/u/credit_world.py
//
// U1R on real PostgreSQL (CREDIT-UNITS, CONSOLE-FLOWS, APP-JOURNEY's usage/balance step). The
// world was built by CONCURRENT requests (credit_world.py): settled, cancelled-with-usage,
// unknown, free, absorbed, running and queued jobs racing on one wallet, a legacy USD job, another
// individual's job, and an exhaustion race that left the wallet low with 402 refusals.
//
// The App's own adapter (`postgrestCreditReads`) runs unchanged against a PostgREST double: each
// call becomes the SQL PostgREST would issue (`json_agg` of the select), executed by `psql` as the
// real browser principal — role `authenticated` with a JWT subject. The results go through the view
// models, and every figure the page would show is compared with a durable query run as the owner.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

import { credits, usd } from "../../lib/format.ts";
import { totalCredit, type Credit } from "../../lib/contracts/v2/money-units.ts";
import {
  postgrestCreditReads,
  spentCredit,
  type Answer,
  type CreditClient,
  type CreditReads,
  type Filter,
} from "../../app/(console)/billing/credit-reads.ts";
import { creditCardState, creditsPageModel, legacyUsdState } from "../../app/(console)/billing/credit-view-model.ts";
import { jobsPageModel, parseJobFilters, jobsPageRequest } from "../../app/(console)/usage/credit-view-model.ts";
import { parsePageCursor } from "../../app/(console)/usage/view-model.ts";

const DSN = process.env.U1R_PG_DSN;
const skip = DSN === undefined ? "U1R_PG_DSN is not set: run tests/u/credit_world.py (task-local PostgreSQL)" : false;
const WORLD = DSN === undefined ? { user: "", other: "", admitted: 0, refused: 0 } : JSON.parse(process.env.U1R_PG_WORLD ?? "{}");

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

/** One statement, as `user` (a JWT subject) or as the owner (`null`), in a rolled-back transaction. */
function psql(user: string | null, statement: string) {
  const impersonate =
    user === null
      ? ""
      : `\\o /dev/null\nset local role authenticated;\nselect set_config('request.jwt.claims', '{"sub":"${user}","role":"authenticated"}', true), set_config('request.jwt.claim.sub', '${user}', true), set_config('request.jwt.claim.role', 'authenticated', true);\n\\o\n`;
  const script = `\\set ON_ERROR_STOP 1\n\\set VERBOSITY verbose\nbegin;\n${impersonate}${statement};\nrollback;\n`;
  return spawnSync("psql", ["-X", "-q", "-A", "-t"], { input: script, env: pgEnv(), encoding: "utf8" });
}

/** Every SELECT the PostgREST double issued, so a case can inspect what reached the database. */
const issued: string[] = [];

/** One statement's rows as JSON, as `user` (a JWT subject) or as the owner (`null`). */
function sql(user: string | null, statement: string): Answer {
  const run = psql(user, `select coalesce(json_agg(t), '[]'::json) from (${statement}) t`);
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

/** The PostgREST double: exactly the calls the adapter makes, rendered as PostgREST's SQL. */
function pgClient(user: string): CreditClient {
  const query = (relation: string, columns: string) => {
    const where: string[] = [];
    const order: string[] = [];
    let limit: number | null = null;
    const run = () => {
      const statement =
        `select ${columns.split(",").map((c) => ident(c.trim())).join(", ")} from public.${ident(relation)}` +
        (where.length ? ` where ${where.join(" and ")}` : "") +
        (order.length ? ` order by ${order.join(", ")}` : "") +
        (limit === null ? "" : ` limit ${limit}`);
      issued.push(statement);
      return sql(user, statement);
    };
    const chain: Filter = {
      eq(c, v) {
        where.push(`${ident(c)} = ${lit(v)}`);
        return chain;
      },
      neq(c, v) {
        where.push(`${ident(c)} <> ${lit(v)}`);
        return chain;
      },
      order(c, o) {
        order.push(`${ident(c)} ${o.ascending ? "asc" : "desc"}`);
        return chain;
      },
      limit(n) {
        limit = n;
        return chain;
      },
      then(ok, bad) {
        return Promise.resolve().then(run).then(ok, bad);
      },
    };
    return chain;
  };
  return {
    from: (relation) => ({ select: (columns) => query(relation, columns) }),
    rpc: (fn, args) =>
      Promise.resolve().then(() => {
        const statement = `select * from public.${ident(fn)}(${Object.entries(args).map(([k, v]) => `${ident(k)} => ${lit(v)}`).join(", ")})`;
        issued.push(statement);
        return sql(user, statement);
      }),
  };
}

/** A durable figure, read as the owner straight from `infrx`. */
function durable<T>(statement: string): T[] {
  const answer = sql(null, statement);
  assert.equal(answer.error, null, JSON.stringify(answer.error));
  return answer.data as T[];
}

function mine() {
  const [row] = durable<{ wallet_id: string; org_id: string; ledger_total: string; reserved_total: string }>(
    `select wallet_id, personal_org_id as org_id, ledger_total::text, reserved_total::text from infrx.credit_wallets where owner_user_id = ${lit(WORLD.user)} and kind = 'consumer'`,
  );
  return row;
}

function paramsOf(href: string): Record<string, string | string[]> {
  const params: Record<string, string | string[]> = {};
  for (const [key, value] of new URL(href, "http://x").searchParams) {
    params[key] = Object.hasOwn(params, key) ? [params[key]].flat().concat(value) : value;
  }
  return params;
}

async function walkJobs(reads: CreditReads, limit: number, now: Date, range = "all", more: Record<string, string> = {}) {
  let filters = parseJobFilters({ range, ...more });
  const jobs = [];
  for (let guard = 0; guard < 100; guard += 1) {
    const result = await reads.jobs({ ...jobsPageRequest(filters, now), limit });
    const model = jobsPageModel({ filters, jobs: result });
    assert.notEqual(model.rows.kind, "error", JSON.stringify(result));
    if (model.rows.kind !== "ready" || !result.ok) break;
    jobs.push(...result.value.items.slice(0, model.rows.value.rows.length).map((job, n) => ({ job, row: model.rows.kind === "ready" ? model.rows.value.rows[n] : null })));
    if (model.rows.value.nextHref === null) break;
    filters = parseJobFilters(paramsOf(model.rows.value.nextHref));
  }
  return jobs;
}

const T = {
  card: "U1R-P01 the balance card equals the durable wallet, reconciles, and the raced wallet reads low with the 402 guidance",
  ledger: "U1R-P02 spent equals -Σ debits, and the ledger walk visits every entry once summing to the balance",
  jobs: "U1R-P03 the usage walk lists every own job once across identical timestamps; Σ charged = spent and Σ held = reserved",
  legacy: "U1R-P04 legacy USD equals the historical USD ledger, labelled USD, never counted in credits",
  tenant: "U1R-P05 another individual's session sees none of this wallet, ledger, jobs or legacy history",
  window: "U1R-P06 the date window is exact on real rows: inside it everything, past it nothing",
  filters: "U1R-P07 the model, key and window filters narrow the rows exactly as the durable jobs say; an empty filter is the unfiltered page",
  cap: "U1R-P08 at the 100 cap each page is 100 rows with a next cursor and the walk is every row once; limit 101 never reaches the database",
};

test(T.card, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const own = mine();
  const wallet = await reads.wallet();
  assert.ok(wallet.ok && wallet.value !== null);
  const [holds] = durable<{ held: string }>(
    `select coalesce(sum(amount), 0)::numeric(20,8)::text as held from infrx.credit_wallet_holds where wallet_id = ${lit(own.wallet_id)} and state in ('held', 'unknown')`,
  );
  const [ledger] = durable<{ total: string }>(
    `select sum(amount)::numeric(20,8)::text as total from infrx.credit_ledger where wallet_id = ${lit(own.wallet_id)}`,
  );
  assert.equal(wallet.value.ledgerTotal, ledger.total, "the wallet total is not its ledger");
  assert.equal(wallet.value.reservedTotal, holds.held, "reserved is not the active holds");
  const card = creditCardState(wallet, await reads.creditsIn(wallet.value.walletId));
  assert.ok(card.kind === "ready" && card.value.reconciles);
  const figure = (label: string) => card.value.figures.find((f) => f.label === label)?.value;
  assert.equal(figure("Reserved"), credits(holds.held));
  assert.equal(figure("Balance"), credits(ledger.total));
  assert.equal(WORLD.refused, 4, "the exhaustion race refused nothing");
  assert.equal(card.value.state.kind, "low", `after the race: ${JSON.stringify(card.value.state)}`);
  assert.equal(figure("Available"), "0.00000001 credits");
  assert.match(card.value.grant, /10,000 credits, received/);
});

test(T.ledger, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const own = mine();
  const [debits] = durable<{ spent: string; n: number }>(
    `select (-coalesce(sum(amount), 0))::numeric(20,8)::text as spent, count(*)::int as n from infrx.credit_ledger where wallet_id = ${lit(own.wallet_id)} and kind = 'inference_debit'`,
  );
  const wallet = await reads.wallet();
  assert.ok(wallet.ok && wallet.value !== null);
  const into = await reads.creditsIn(own.wallet_id);
  assert.ok(into.ok);
  assert.equal(spentCredit(wallet.value, into.value), debits.spent);
  assert.ok(debits.n >= 5, `${debits.n} debits: the concurrent settlements did not all land`);
  let state = parsePageCursor({});
  const ids: string[] = [];
  const amounts: Credit[] = [];
  for (let guard = 0; guard < 100; guard += 1) {
    const ledger = await reads.ledger({ limit: 2, cursor: state.cursor });
    const model = creditsPageModel({ state, wallet, creditsIn: into, ledger, legacy: null });
    assert.ok(model.ledger.kind === "ready" && ledger.ok, JSON.stringify(ledger));
    ids.push(...model.ledger.value.rows.map((r) => r.id));
    amounts.push(...ledger.value.items.map((e) => e.amount));
    if (model.ledger.value.nextHref === null) break;
    state = parsePageCursor(paramsOf(model.ledger.value.nextHref));
  }
  const [count] = durable<{ n: number }>(`select count(*)::int as n from infrx.credit_ledger where wallet_id = ${lit(own.wallet_id)}`);
  assert.equal(ids.length, count.n, "an entry was lost");
  assert.equal(new Set(ids).size, ids.length, "an entry was repeated");
  assert.equal(totalCredit(amounts), own.ledger_total);
  // WR-3(c) / C0 WR-5 (0024): every page is consumer_credit_ledger - one index range stopped by its
  // LIMIT, no `actor` (the view ran visible_principal() on every wallet row: 1.5 s a page at 10k) -
  // and none is a top-N sort of the security-barrier view.
  const pages = issued.filter((q) => q.includes("public.consumer_credit_ledger("));
  assert.ok(pages.length > 1, "no ledger page reached the database");
  assert.ok(!issued.some((q) => q.includes("from public.console_credit_ledger") && q.includes(" order by ")), "a ledger page read the view");
  // WR-3(b): "Spent" reads only the wallet's grant and adjustments, through their own partial index.
  const creditsIn = issued.find((q) => q.includes("kind <> 'inference_debit'"));
  assert.ok(creditsIn !== undefined, "the credits-in read did not reach the database");
  const inPlan = psql(WORLD.user, `set local enable_seqscan = off; explain (costs off) ${creditsIn}`);
  assert.equal(inPlan.status, 0, inPlan.stderr);
  assert.match(inPlan.stdout, /credit_ledger_wallet_credits_in_idx/, "credits-in filters every wallet entry by kind");
});

test(T.jobs, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const own = mine();
  const now = new Date();
  const expected = durable<{ request_id: string }>(`select request_id::text from infrx.jobs where org_id = ${lit(own.org_id)}`);
  const [debits] = durable<{ spent: string }>(
    `select (-coalesce(sum(amount), 0))::numeric(20,8)::text as spent from infrx.credit_ledger where wallet_id = ${lit(own.wallet_id)} and kind = 'inference_debit'`,
  );
  const [ties] = durable<{ n: number }>(`select count(distinct created_at)::int as n from infrx.jobs where org_id = ${lit(own.org_id)}`);
  assert.ok(ties.n < expected.length, "the world has no timestamp ties to page across");
  for (const limit of [1, 2, 7]) {
    const walked = await walkJobs(reads, limit, now);
    const ids = walked.map((w) => w.job.requestId);
    assert.equal(new Set(ids).size, ids.length, `limit ${limit}: a job was repeated`);
    assert.deepEqual([...ids].sort(), expected.map((e) => e.request_id).sort(), `limit ${limit}: the walk is not the org's jobs`);
    const credit = walked.filter((w) => w.job.unit === "CREDIT");
    const charged = credit.filter((w) => w.job.settlementState === "settled").map((w) => w.job.charged as Credit);
    const held = credit.filter((w) => w.job.holdState === "held" || w.job.holdState === "unknown").map((w) => w.job.hold as Credit);
    assert.equal(totalCredit(charged), debits.spent, `limit ${limit}: Σ charged ≠ spent`);
    assert.equal(totalCredit(held), own.reserved_total, `limit ${limit}: Σ held ≠ reserved`);
    for (const { job, row } of walked) {
      assert.ok(row !== null);
      if (job.settlementState !== "settled") assert.equal(row.charge.amount, null, `${job.requestId} shows a charge while ${job.settlementState}`);
      if (job.unit === "USD") assert.match(`${row.unit} ${row.charge.held}`, /legacy USD \$[\d.,]+ USD/);
      else assert.doesNotMatch(JSON.stringify(row.charge), /\$|USD/);
    }
    const states = new Set(walked.map((w) => w.job.settlementState ?? "pending"));
    for (const s of ["settled", "held_unknown", "released_free", "released_platform_absorbed", "pending"]) {
      assert.ok(states.has(s), `the walk has no ${s} job`);
    }
  }
});

test(T.legacy, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const own = mine();
  const [history] = durable<{ balance: string; n: number }>(
    `select sum(delta_usd)::numeric(20,8)::text as balance, count(*)::int as n from public.credit_ledger where org_id = ${lit(own.org_id)}`,
  );
  const legacy = legacyUsdState(await reads.legacyUsd(own.org_id));
  assert.ok(legacy.kind === "ready");
  assert.equal(legacy.value.balance, usd(history.balance));
  assert.equal(legacy.value.entries, history.n);
  assert.ok(legacy.value.hold !== null, "a nonzero legacy balance is a rollout hold (R72)");
});

test(T.tenant, { skip }, async () => {
  const own = mine();
  const theirs = postgrestCreditReads(pgClient(WORLD.other), WORLD.other);
  const wallet = await theirs.wallet();
  assert.ok(wallet.ok && wallet.value !== null && wallet.value.walletId !== own.wallet_id);
  const walked = await walkJobs(theirs, 5, new Date());
  const mineIds = new Set(durable<{ request_id: string }>(`select request_id::text from infrx.jobs where org_id = ${lit(own.org_id)}`).map((r) => r.request_id));
  assert.ok(walked.length >= 1, "the other individual's own job is missing");
  assert.ok(walked.every((w) => !mineIds.has(w.job.requestId)), "another individual's job was listed");
  const mineEntries = new Set(durable<{ entry_id: string }>(`select entry_id::text from infrx.credit_ledger where wallet_id = ${lit(own.wallet_id)}`).map((r) => r.entry_id));
  const ledger = await theirs.ledger({ limit: 50, cursor: null });
  assert.ok(ledger.ok, JSON.stringify(ledger));
  assert.ok(ledger.value.items.every((e) => !mineEntries.has(e.id)), "another individual's ledger entry was listed");
  const into = await theirs.creditsIn(own.wallet_id);
  assert.deepEqual(into, { ok: true, value: "0.00000000" });
  const legacy = await theirs.legacyUsd(own.org_id);
  assert.equal(legacy.ok ? null : legacy.error.code, "forbidden");
});

test(T.window, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const [clock] = durable<{ at: string }>(`select max(created_at)::text as at from infrx.jobs`);
  const newest = new Date(clock.at.replace(" ", "T").replace(/\+00$/, "Z"));
  const inside = await walkJobs(reads, 3, new Date(newest.getTime() + 60_000), "24h");
  const all = await walkJobs(reads, 3, new Date(), "all");
  assert.equal(inside.length, all.length);
  const later = await walkJobs(reads, 3, new Date(newest.getTime() + 8 * 86_400_000), "7d");
  assert.equal(later.length, 0);
});

test(T.filters, { skip }, async () => {
  const reads = postgrestCreditReads(pgClient(WORLD.user), WORLD.user);
  const own = mine();
  const ids = (walked: { job: { requestId: string } }[]) => walked.map((w) => w.job.requestId).sort();
  const expect = (where: string) =>
    durable<{ request_id: string }>(`select request_id::text from infrx.jobs where org_id = ${lit(own.org_id)} and ${where}`)
      .map((r) => r.request_id)
      .sort();
  const all = ids(await walkJobs(reads, 3, new Date()));
  assert.deepEqual(all, expect("true"));
  // An empty filter (the form's "all" key and blank model) is the unfiltered page, call for call.
  assert.deepEqual(ids(await walkJobs(reads, 3, new Date(), "all", { key: "all", model: "" })), all);
  const unfiltered = await reads.jobs({ limit: 100, cursor: null });
  assert.deepEqual(await reads.jobs({ limit: 100, cursor: null, model: null, keyId: null, from: null, to: null }), unfiltered);

  // Model: the requested string or the canonical revision, each narrowing to exactly its rows.
  const models = durable<{ m: string }>(`select distinct m from infrx.jobs j, lateral (values (j.requested_model), (j.model_revision)) v(m) where org_id = ${lit(own.org_id)} and m is not null`);
  assert.ok(models.length >= 1, "the world's jobs name no model");
  for (const { m } of models) {
    const got = ids(await walkJobs(reads, 3, new Date(), "all", { model: m }));
    assert.deepEqual(got, expect(`${lit(m)} in (requested_model, model_revision)`), `model ${m}`);
    assert.ok(got.length >= 1);
  }
  assert.deepEqual(ids(await walkJobs(reads, 3, new Date(), "all", { model: "no/such-model" })), []);

  // Key: own key, another individual's key and an unknown key.
  const keys = durable<{ key_id: string }>(`select distinct key_id::text from infrx.jobs where org_id = ${lit(own.org_id)} and key_id is not null`);
  assert.ok(keys.length >= 1);
  for (const { key_id } of keys) {
    assert.deepEqual(ids(await walkJobs(reads, 3, new Date(), "all", { key: key_id })), expect(`key_id = ${lit(key_id)}`), `key ${key_id}`);
  }
  const [theirKey] = durable<{ key_id: string }>(`select key_id::text from infrx.jobs where org_id <> ${lit(own.org_id)} and key_id is not null limit 1`);
  assert.deepEqual(ids(await walkJobs(reads, 3, new Date(), "all", { key: theirKey.key_id })), [], "another individual's key lists nothing");

  // Window [now - 30d, now): `now` at the newest job's instant (the harness clock is frozen, so
  // jobs share instants) leaves those jobs out - the end is exclusive - and 1 ms later lets them in.
  const [{ at }] = durable<{ at: string }>(`select to_char(max(created_at) at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as at from infrx.jobs where org_id = ${lit(own.org_id)}`);
  const now = new Date(at);
  const bound = now.toISOString().replace(/Z$/, "000Z");
  const windowed = ids(await walkJobs(reads, 3, now, "30d"));
  const inside = expect(`created_at >= ${lit(bound)}::timestamptz - interval '30 days' and created_at < ${lit(bound)}::timestamptz`);
  assert.deepEqual(windowed, inside);
  assert.ok(inside.length < all.length, `the newest jobs are at or after the window's end: ${inside.length} of ${all.length}`);
  assert.deepEqual(ids(await walkJobs(reads, 3, new Date(now.getTime() + 1), "30d")), all);
  // Combined: the window and the model together.
  const [{ m: first }] = models;
  assert.deepEqual(
    ids(await walkJobs(reads, 3, now, "30d", { model: first })),
    expect(`${lit(first)} in (requested_model, model_revision) and created_at >= ${lit(bound)}::timestamptz - interval '30 days' and created_at < ${lit(bound)}::timestamptz`),
  );
});

test(T.cap, { skip }, async () => {
  // CONSUMER_2 has 120+ jobs and ledger entries (credit_world.py). Before R146's clamp the adapter
  // asked for 101: consumer_credit_ledger refused it (dependency_unavailable) and consumer_jobs
  // clamped it to 100 silently, so the full page had no next cursor.
  const reads = postgrestCreditReads(pgClient(WORLD.other), WORLD.other);
  const [theirs] = durable<{ wallet_id: string; org_id: string }>(
    `select wallet_id, personal_org_id as org_id from infrx.credit_wallets where owner_user_id = ${lit(WORLD.other)} and kind = 'consumer'`,
  );
  const durableIds = (statement: string) => durable<{ id: string }>(statement).map((r) => r.id).sort();
  for (const [name, read, expected] of [
    ["ledger", reads.ledger, durableIds(`select entry_id::text as id from infrx.credit_ledger where wallet_id = ${lit(theirs.wallet_id)}`)],
    ["jobs", reads.jobs, durableIds(`select request_id::text as id from infrx.jobs where org_id = ${lit(theirs.org_id)}`)],
  ] as const) {
    assert.ok(expected.length > 100, `${name}: the world has only ${expected.length} rows`);
    const first = await read({ limit: 100, cursor: null });
    assert.ok(first.ok, `${name}: ${JSON.stringify(first)}`);
    assert.equal(first.value.items.length, 100);
    assert.notEqual(first.value.next_cursor, null, `${name}: a full page at the cap has no next cursor`);
    const rest = await read({ limit: 100, cursor: first.value.next_cursor });
    assert.ok(rest.ok, `${name}: ${JSON.stringify(rest)}`);
    assert.equal(rest.value.next_cursor, null);
    const items = [...first.value.items, ...rest.value.items] as ({ id: string } | { requestId: string })[];
    assert.deepEqual(items.map((i) => ("id" in i ? i.id : i.requestId)).sort(), expected, `${name}: the capped walk is not every row once`);
    const sent = issued.length;
    const refused = await read({ limit: 101, cursor: null });
    assert.equal(refused.ok ? null : refused.error.code, "invalid_request");
    assert.equal(issued.length, sent, `${name}: limit 101 reached the database`);
  }
});
