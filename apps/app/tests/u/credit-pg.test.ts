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
      or(filter) {
        const keyset = /^created_at\.lt\."([^"]+)",and\(created_at\.eq\."([^"]+)",entry_id\.lt\.([0-9a-f-]{36})\)$/.exec(filter);
        if (keyset === null || keyset[1] !== keyset[2]) throw new TypeError(`unsupported filter ${filter}`);
        where.push(`(created_at < ${lit(keyset[1])} or (created_at = ${lit(keyset[1])} and entry_id < ${lit(keyset[3])}))`);
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
      Promise.resolve().then(() =>
        sql(user, `select * from public.${ident(fn)}(${Object.entries(args).map(([k, v]) => `${ident(k)} => ${lit(v)}`).join(", ")})`),
      ),
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

async function walkJobs(reads: CreditReads, limit: number, now: Date, range = "all") {
  let filters = parseJobFilters({ range });
  const jobs = [];
  for (let guard = 0; guard < 100; guard += 1) {
    const result = await reads.jobs({ ...jobsPageRequest(filters), limit });
    const model = jobsPageModel({ filters, jobs: result, now });
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
    const ledger = await reads.ledger(own.wallet_id, { limit: 2, cursor: state.cursor });
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
  // The page stays indexed: selecting `actor` makes the view run visible_principal() on every row of
  // the wallet before the sort and limit (1.5 s a page at 10k entries). Neither the select list nor
  // the plan PostgreSQL chose for the browser principal may carry it.
  const pages = issued.filter((q) => q.includes("from public.console_credit_ledger") && q.includes(" order by "));
  assert.ok(pages.length > 1, "no ledger page reached the database");
  for (const page of pages) assert.doesNotMatch(page.slice(0, page.indexOf(" from ")), /\bactor\b/, page);
  const plan = psql(WORLD.user, `explain (verbose, costs off) ${pages[0]}`);
  assert.equal(plan.status, 0, plan.stderr);
  assert.doesNotMatch(plan.stdout, /visible_principal/, "the ledger page evaluates visible_principal() per row");
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
  const ledger = await theirs.ledger(own.wallet_id, { limit: 5, cursor: null });
  assert.ok(ledger.ok && ledger.value.items.length === 0, "a guessed wallet id read another ledger");
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
