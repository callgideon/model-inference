// node --test "tests/**/*.test.ts"
//
// U1R Usage page view model (`app/(console)/usage/credit-view-model.ts`) over `consumer_jobs`:
// status, model/revision, tokens, and the charge by settlement state in each job's own unit. A hold
// is not a charge, unknown usage is not a zero, legacy USD rows say USD, the model/key/window filters
// are consumer_jobs' own (0024), and a keyset walk visits every job once so its totals match the wallet.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { credits } from "../../lib/format.ts";
import { totalCredit, type Credit } from "../../lib/contracts/v2/money-units.ts";
import type { Result, Page } from "../../lib/contracts/types.ts";
import type { ConsumerJob } from "../../app/(console)/billing/credit-reads.ts";
import { defaultCreditFixture, fixtureCreditReads } from "../../app/(console)/billing/credit-fixture.ts";
import {
  DEFAULT_JOB_RANGE,
  JOB_PAGE_SIZE,
  jobChargeView,
  jobRowView,
  jobsHref,
  jobsPageModel,
  jobsPageRequest,
  parseJobFilters,
  type JobFilters,
} from "../../app/(console)/usage/credit-view-model.ts";

const NOW = new Date("2026-09-20T12:00:00.000Z");
const fixture = defaultCreditFixture();
const byN = (n: number) => fixture.jobs.find((j) => j.requestId.endsWith(`0${n}`))!;

const T = {
  hold: "U1R-U01 a pending or unreconciled job shows its hold and no charge; only a settled job shows a charged amount",
  units: "U1R-U02 each row is in its own unit: credits never carry '$', legacy USD always says USD",
  tokens: "U1R-U03 unreported usage shows no token count and says nothing is estimated",
  status: "U1R-U04 state, cause and settlement each read as what they are, and an unknown settlement is not a charge",
  window: "U1R-U05 the window is [now - range, now) and goes to consumer_jobs with the model and key; unknown URL values are no filter",
  hrefs: "U1R-U06 every href is computed here: pages carry the filters, applying filters resets the cursor, rows link to their detail",
  states: "U1R-U07 a failed read is an error with its recovery, and an empty window says so",
  walk: "U1R-U08 walking every page visits each job once, and the totals equal the wallet's spent and reserved",
};

test(T.hold, () => {
  const pending = jobChargeView(byN(3));
  assert.equal(pending.amount, null);
  assert.equal(pending.held, "5.00 credits");
  assert.match(pending.label, /pending/i);
  const unknown = jobChargeView(byN(4));
  assert.equal(unknown.amount, null);
  assert.equal(unknown.held, "5.00 credits");
  assert.match(unknown.detail, /not a charge/);
  const settled = jobChargeView(byN(1));
  assert.equal(settled.amount, "1.23456789 credits");
  assert.equal(settled.held, null, "a settled hold is gone, not still held");
  // A settled job whose charge could not be read is "unavailable", never a zero.
  const lost = jobChargeView({ ...byN(1), charged: null });
  assert.equal(lost.amount, "Unavailable");
  for (const n of [5, 6]) {
    const free = jobChargeView(byN(n));
    assert.equal(free.amount, null);
    assert.equal(free.held, null);
    assert.match(free.label, /no charge/i);
  }
});

test(T.units, () => {
  const legacy = jobRowView(byN(8));
  assert.equal(legacy.unit, "legacy USD");
  assert.equal(legacy.charge.amount, "$0.0001966 USD");
  const credit = jobRowView(byN(1));
  assert.equal(credit.unit, "credits");
  for (const n of [1, 2, 3, 4, 5, 6, 7]) {
    const row = jobRowView(byN(n));
    assert.doesNotMatch(JSON.stringify(row.charge), /\$|USD/, `job ${n}`);
  }
});

test(T.tokens, () => {
  const unknown = jobRowView(byN(4)).tokens;
  assert.deepEqual([unknown.input, unknown.output], ["—", "—"]);
  assert.match(unknown.note ?? "", /not reported/i);
  // Counts present but not authoritative are still not shown as usage.
  const estimated = jobRowView({ ...byN(1), usageCertainty: "unknown" }).tokens;
  assert.deepEqual([estimated.input, estimated.output], ["—", "—"]);
  const known = jobRowView(byN(1)).tokens;
  assert.deepEqual([known.input, known.output, known.note], ["1,200", "340", null]);
});

test(T.status, () => {
  assert.equal(jobRowView(byN(1)).status, "Succeeded");
  assert.equal(jobRowView(byN(3)).status, "Running");
  assert.equal(jobRowView(byN(4)).status, "Failed · engine incomplete");
  assert.equal(jobRowView(byN(7)).status, "Cancelled · client cancelled");
  assert.equal(jobRowView({ ...byN(5), state: "expired", outcomeCause: "queue_wait_expired" }).status, "Expired · queue wait expired");
  const odd = jobChargeView({ ...byN(1), settlementState: "something_new" });
  assert.equal(odd.amount, null);
  assert.match(odd.label, /unknown/i);
  // Only a hold that is still held or unknown is shown as held; a released one is gone.
  assert.equal(jobChargeView({ ...byN(5), settlementState: "something_new" }).held, null);
  assert.equal(jobChargeView({ ...byN(3), settlementState: "something_new" }).held, "5.00 credits");
  const row = jobRowView(byN(1));
  assert.equal(row.model, "nemostation/marlin-2b");
  assert.equal(row.revision, "nemostation/marlin-2b@2026-09-01");
  assert.equal(row.when, "2026-09-20 11:00 UTC");
});

/** A link's query as Next hands it to a page: a repeated parameter becomes an array. */
function paramsOf(href: string): Record<string, string | string[]> {
  const params: Record<string, string | string[]> = {};
  for (const [key, value] of new URL(href, "http://x").searchParams) {
    params[key] = Object.hasOwn(params, key) ? [params[key]].flat().concat(value) : value;
  }
  return params;
}

function page(items: ConsumerJob[], next: string | null = null): Result<Page<ConsumerJob>> {
  return { ok: true, value: { items, next_cursor: next } };
}

test(T.window, () => {
  const KEY = "c7000000-0000-4000-8000-0000000000c1";
  assert.deepEqual(jobsPageRequest(parseJobFilters({ range: "24h", key: KEY, model: " m@1 " }), NOW), {
    limit: JOB_PAGE_SIZE,
    cursor: null,
    model: "m@1",
    keyId: KEY,
    from: "2026-09-19T12:00:00.000000Z",
    to: "2026-09-20T12:00:00.000000Z",
  });
  assert.deepEqual(jobsPageRequest(parseJobFilters({}), NOW), {
    limit: JOB_PAGE_SIZE, cursor: null, model: null, keyId: null, from: null, to: null,
  }, "no filter is no argument: the unfiltered read");
  assert.equal(parseJobFilters({}).range, DEFAULT_JOB_RANGE);
  assert.equal(parseJobFilters({ range: "toString" }).range, DEFAULT_JOB_RANGE);
  assert.equal(parseJobFilters({ range: "5m" }).range, DEFAULT_JOB_RANGE);
  for (const key of ["all", "", "x", `${KEY}'`, KEY.toUpperCase()]) assert.equal(parseJobFilters({ key }).keyId, null, key);
  for (const model of ["", "   ", "m".repeat(201)]) assert.equal(parseJobFilters({ model }).model, null);
  assert.equal(parseJobFilters({ model: ["a", "b"] }).model, "b", "a repeated parameter: the last one");
  // The fixture applies the window as consumer_jobs does: at its start in, at its end out.
  const start = { ...byN(8), createdAt: "2026-09-19T12:00:00.000000Z" };
  const end = { ...byN(1), createdAt: "2026-09-20T12:00:00.000000Z" };
  const reads = fixtureCreditReads({ ...fixture, jobs: [start, end] });
  return reads.jobs(jobsPageRequest(parseJobFilters({ range: "24h" }), NOW)).then((got) => {
    assert.deepEqual(got.ok && got.value.items.map((j) => j.requestId), [start.requestId]);
  });
});

test(T.hrefs, () => {
  const KEY = "c7000000-0000-4000-8000-0000000000c1";
  const first = jobsPageModel({ filters: parseJobFilters({ range: "7d", key: KEY, model: "m" }), jobs: page([byN(1)], "C1") });
  assert.equal(first.here, `/usage?range=7d&key=${KEY}&model=m`);
  assert.ok(first.rows.kind === "ready");
  if (first.rows.kind !== "ready") return;
  assert.equal(first.rows.value.nextHref, `/usage?range=7d&key=${KEY}&model=m&cursor=C1`, "the next page keeps the filters");
  assert.equal(first.rows.value.previousHref, null);
  assert.equal(first.rows.value.rows[0].detailHref, `/usage/${byN(1).requestId}`);
  const deep: JobFilters = parseJobFilters({ range: "7d", cursor: "C2", trail: ["C1"] });
  assert.equal(jobsHref(deep), "/usage?range=7d&cursor=C2&trail=C1");
  assert.equal(jobsHref(parseJobFilters({})), "/usage");
  assert.equal(jobsPageRequest(deep, NOW).cursor, "C2");
  // Applying filters is a GET of the form: its fields are the filters only, so the walk restarts.
  const form = readFileSync("app/(console)/usage/usage-controls.tsx", "utf8");
  assert.deepEqual([...form.matchAll(/name="([a-z]+)"/g)].map((m) => m[1]), ["range", "key", "model"]);
  assert.match(form, /<form action="\/usage"/);
});

test(T.states, () => {
  const failed = jobsPageModel({
    filters: parseJobFilters({}),
    jobs: { ok: false, error: { code: "dependency_unavailable", message: "x" } },
  });
  assert.ok(failed.rows.kind === "error" && failed.rows.recovery === "retry");
  const stale = jobsPageModel({
    filters: parseJobFilters({ cursor: "C9" }),
    jobs: { ok: false, error: { code: "invalid_cursor", message: "x" } },
  });
  assert.ok(stale.rows.kind === "error" && stale.rows.recovery === "restart");
  assert.equal(stale.firstHref, "/usage");
  const empty = jobsPageModel({ filters: parseJobFilters({}), jobs: page([]) });
  assert.equal(empty.rows.kind, "empty");
  assert.match(empty.emptyText, /no requests yet/i);
  const emptyWindow = jobsPageModel({ filters: parseJobFilters({ range: "24h" }), jobs: page([]) });
  assert.match(emptyWindow.emptyText, /last 24h/);
  const emptyFilter = jobsPageModel({ filters: parseJobFilters({ range: "24h", model: "m" }), jobs: page([]) });
  assert.match(emptyFilter.emptyText, /match this model or key/);
});

test(T.walk, async () => {
  const reads = fixtureCreditReads(fixture);
  for (const limit of [1, 2, 3, 100]) {
    let filters = parseJobFilters({});
    const seen: string[] = [];
    const charged: Credit[] = [];
    const held: Credit[] = [];
    for (let guard = 0; guard < 50; guard += 1) {
      const result = await reads.jobs({ limit, cursor: filters.cursor });
      const model = jobsPageModel({ filters, jobs: result });
      if (model.rows.kind !== "ready") break;
      for (const row of model.rows.value.rows) seen.push(row.requestId);
      assert.ok(result.ok);
      for (const job of result.value.items) {
        if (job.unit !== "CREDIT") continue;
        if (job.settlementState === "settled") charged.push(job.charged as Credit);
        if (job.holdState === "held" || job.holdState === "unknown") held.push(job.hold as Credit);
      }
      if (model.rows.value.nextHref === null) break;
      filters = parseJobFilters(paramsOf(model.rows.value.nextHref));
    }
    assert.equal(seen.length, fixture.jobs.length, `limit ${limit}: a job was lost or repeated`);
    assert.equal(new Set(seen).size, seen.length, `limit ${limit}: a job was repeated`);
    const wallet = fixture.wallet!;
    const debits = fixture.ledger.filter((e) => e.kind === "inference_debit").map((e) => e.amount);
    // Σ charged over the walk = −Σ debits on the ledger; Σ holds over the walk = reserved.
    assert.equal(credits(totalCredit(charged)), credits(totalCredit(debits).replace("-", "") as Credit));
    assert.equal(totalCredit(held), wallet.reservedTotal);
  }
});
