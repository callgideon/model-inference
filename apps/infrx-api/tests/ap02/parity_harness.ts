/**
 * AP-02 parity: the App's CURRENT read adapters (credit-reads.ts, request-reads.ts,
 * request-view-model.ts, operator-reads.ts) run unchanged against the same seeded ap2 database
 * the new API reads, through a client that does what PostgREST does with the browser's session:
 * `set local role authenticated`, the user as the JWT subject (both claim forms), and
 * `coalesce(json_agg(t), '[]')` / `to_json(fn(...))` for the JSON (PostgREST's own rendering).
 * Prints one JSON document; `test_parity_pg.py` compares it with the API's answers.
 *
 * Inputs (env): PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE (task-local), AP02_USER, AP02_ORG,
 * AP02_OPERATOR. Run: node tests/ap02/parity_harness.ts
 */
import { execFile } from "node:child_process";
import { postgrestCreditReads, spentCredit } from "../../../app/app/(console)/billing/credit-reads.ts";
import { postgrestRequestReads } from "../../../app/app/(console)/usage/[requestId]/request-reads.ts";
import { resultAccessOf } from "../../../app/app/(console)/usage/[requestId]/request-view-model.ts";
import { operatorReads } from "../../../app/app/(console)/admin/operator-reads.ts";

type Answer = { data: unknown; error: { code?: string | null; message?: string | null } | null };
const SCALAR = new Set(["consumer_job_result"]);

const lit = (v: unknown): string =>
  v === null || v === undefined ? "null" : typeof v === "number" || typeof v === "boolean"
    ? String(v) : `'${String(v).replace(/'/g, "''")}'`;
const ident = (c: string): string => `"${c.replace(/"/g, '""')}"`;

function as(user: string, sql: string): Promise<Answer> {
  const claims = JSON.stringify({ sub: user, role: "authenticated" });
  const args = ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose",
    "-c", "begin", "-c", "set local role authenticated",
    "-c", `select 1 where set_config('request.jwt.claims', ${lit(claims)}, true) is null ` +
          `or set_config('request.jwt.claim.sub', ${lit(user)}, true) is null ` +
          `or set_config('request.jwt.claim.role', 'authenticated', true) is null`,
    "-c", sql, "-c", "rollback"];
  return new Promise((resolve) => {
    execFile("psql", args, { env: { ...process.env, PGTZ: "UTC" } }, (err, stdout, stderr) => {
      if (err) {
        const m = /ERROR:\s+([0-9A-Z]{5}): (.*)/.exec(stderr);
        resolve({ data: null, error: { code: m?.[1] ?? "unknown", message: m?.[2] ?? stderr } });
      } else {
        resolve({ data: JSON.parse(stdout.trim()), error: null });
      }
    });
  });
}

/** The supabase-js slice the adapters call, rendered to SQL as PostgREST would run it. */
function client(user: string) {
  return {
    from(relation: string) {
      return {
        select(columns: string) {
          const where: string[] = [];
          const order: string[] = [];
          let limit = "";
          const q = {
            eq(c: string, v: string) { where.push(`${ident(c)} = ${lit(v)}`); return q; },
            neq(c: string, v: string) { where.push(`${ident(c)} <> ${lit(v)}`); return q; },
            in(c: string, vs: string[]) { where.push(`${ident(c)} in (${vs.map(lit).join(", ")})`); return q; },
            order(c: string, o: { ascending: boolean }) { order.push(`${ident(c)} ${o.ascending ? "asc" : "desc"}`); return q; },
            limit(n: number) { limit = ` limit ${Number(n)}`; return q; },
            then(ok: (a: Answer) => unknown, no?: (e: unknown) => unknown) {
              const sql = `select ${columns} from public.${ident(relation)}` +
                (where.length ? ` where ${where.join(" and ")}` : "") +
                (order.length ? ` order by ${order.join(", ")}` : "") + limit;
              return as(user, `select coalesce(json_agg(t), '[]'::json) from (${sql}) t`).then(ok, no);
            },
          };
          return q;
        },
      };
    },
    rpc(fn: string, args: Record<string, unknown>) {
      const call = `public.${ident(fn)}(${Object.entries(args).map(([k, v]) => `${k} => ${lit(v)}`).join(", ")})`;
      return as(user, SCALAR.has(fn) ? `select to_json(${call})`
        : `select coalesce(json_agg(t), '[]'::json) from ${call} t`);
    },
  };
}

async function walk<T>(read: (cursor: string | null) => Promise<any>): Promise<T[] | unknown> {
  const items: T[] = [];
  let cursor: string | null = null;
  for (;;) {
    const page = await read(cursor);
    if (!page.ok) return page;
    items.push(...page.value.items);
    cursor = page.value.next_cursor;
    if (cursor === null) return items;
  }
}

const user = process.env.AP02_USER as string;
const org = process.env.AP02_ORG as string;
const c = client(user) as any;
const reads = postgrestCreditReads(c, user);
const wallet: any = await reads.wallet();
const creditsIn: any = wallet.ok && wallet.value ? await reads.creditsIn(wallet.value.walletId) : null;
const jobs: any = await walk((cursor) => reads.jobs({ limit: 2, cursor }));
const requestReads = postgrestRequestReads(c, user);
const results: Record<string, unknown> = {};
for (const job of Array.isArray(jobs) ? jobs : []) results[job.requestId] = await requestReads.result(job.requestId);
const out = {
  wallet,
  spent: wallet.ok && wallet.value && creditsIn?.ok ? spentCredit(wallet.value, creditsIn.value) : null,
  ledger: await walk((cursor) => reads.ledger({ limit: 2, cursor })),
  legacy: await reads.legacyUsd(org),
  jobs,
  access: Object.fromEntries((Array.isArray(jobs) ? jobs : []).map((j: any) => [j.requestId, resultAccessOf(j)])),
  results,
  keys: await reads.keys(org),
  operator: await operatorReads(client(process.env.AP02_OPERATOR as string) as any),
};
process.stdout.write(JSON.stringify(out));
