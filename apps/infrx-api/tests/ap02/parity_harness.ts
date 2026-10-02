/**
 * AP-02 parity, re-pointed by AP-09 (WR-AP09-PARITY): the App's read adapters as they now ship
 * (credit-reads.ts `apiCreditReads`, request-reads.ts `apiRequestReads`, request-view-model.ts,
 * operator-reads.ts `operatorReads`) run unchanged under node against the API's own routes, served
 * over HTTP on the same seeded ap2 database (`test_parity_pg.py` starts them). The App holds no
 * database client any more, so this is the composed proof that the App's mappers accept exactly
 * what the API answers on real PostgreSQL. Prints one JSON document; `test_parity_pg.py` compares
 * it with the API's answers.
 *
 * Inputs (env): AP02_API_ME, AP02_API_OPERATOR (the routes as the individual / the operator).
 * Run: node tests/ap02/parity_harness.ts
 */
import { consumerApi } from "../../../app/lib/api/index.ts";
import { apiCreditReads } from "../../../app/app/(console)/billing/credit-reads.ts";
import { apiRequestReads } from "../../../app/app/(console)/usage/[requestId]/request-reads.ts";
import { resultAccessOf } from "../../../app/app/(console)/usage/[requestId]/request-view-model.ts";
import { operatorReads } from "../../../app/app/(console)/admin/operator-reads.ts";

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

const me = consumerApi({ baseUrl: process.env.AP02_API_ME as string });
const reads = apiCreditReads(me);
const wallet: any = await reads.wallet();
const jobs: any = await walk((cursor) => reads.jobs({ limit: 2, cursor }));
const requestReads = apiRequestReads(me);
const results: Record<string, unknown> = {};
for (const job of Array.isArray(jobs) ? jobs : []) results[job.requestId] = await requestReads.result(job.requestId);
const out = {
  wallet,
  spent: wallet.ok && wallet.value ? wallet.value.spent : null,
  ledger: await walk((cursor) => reads.ledger({ limit: 2, cursor })),
  legacy: await reads.legacyUsd(),
  jobs,
  access: Object.fromEntries((Array.isArray(jobs) ? jobs : []).map((j: any) => [j.requestId, resultAccessOf(j)])),
  results,
  keys: await reads.keys(),
  operator: await operatorReads(consumerApi({ baseUrl: process.env.AP02_API_OPERATOR as string })),
};
process.stdout.write(JSON.stringify(out));
