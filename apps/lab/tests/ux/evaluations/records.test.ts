// UX-08 WR-UX08-3: the judge page's records over AP-08's list reads (`GET /lab/v1/judge/configs`,
// `/runs`, `/budgets`) through the generated client. The fake API stands for the routes and records
// what was sent. The read asks for the guarded workspace only and fails closed: any refusal, or any
// answer it cannot read whole, is null (the section's unavailable state), never a partial set read as
// complete; a list with a further page is shown with `more` so the page says so.
import assert from "node:assert/strict";
import test from "node:test";
import { labApi } from "../../../lib/api/index.ts";
import { readJudgeRecords } from "../../../lib/services/judge/records.ts";

const A = "a0000000-0000-4000-8000-00000000000a";
const dev = { providerId: A, role: "developer" as const };
const USD = (amount: string) => ({ amount, unit: "PROVIDER_USD" });
const CAL = { state: "insufficient", labels: 3, required: 20, agreement: null, interval: null };
const CONFIG = { config_id: "c1", model_id: "m1", judge_model: "judge-x", rubric_version: 2, sample_size: 10, grantor_org_id: "g1", created_at: "2026-10-01T00:00:00Z", calibration: CAL };
const RUN = {
  run_id: "r1", config_id: "c1", domain_state: "submitted", cancel_requested: false, payer_ref: "payer-a", sample_size: 10,
  selected: 8, sent: 8, accepted: 7, rejected: 1, requested_at: "2026-10-02T00:00:00Z", reserved: USD("0.40000000"), settled: null,
};
const BUDGET = { payer_ref: "payer-a", limit: USD("5.00000000"), reserved: USD("0.40000000"), settled: USD("0.00000000"), version: 1 };
const PAGES: Record<string, unknown> = { configs: { data: [CONFIG], next_cursor: null }, runs: { data: [RUN], next_cursor: null }, budgets: { data: [BUDGET] } };

type Answer = { status?: number; body?: unknown; throws?: boolean };
function server(answers: Record<string, Answer> = {}) {
  const sent: { path: string; query: Record<string, string> }[] = [];
  const fetch = (async (url: string) => {
    const u = new URL(url);
    const list = u.pathname.split("/").pop()!;
    sent.push({ path: u.pathname, query: Object.fromEntries(u.searchParams) });
    const a = answers[list] ?? {};
    if (a.throws) throw new Error("transport");
    return new Response(JSON.stringify(a.body ?? PAGES[list]), { status: a.status ?? 200 });
  }) as typeof globalThis.fetch;
  return { api: labApi({ baseUrl: "https://lab-control.example", fetch, session: () => ({ token: "tok" }) }), sent };
}

test("UX08-K01 a workspace's configurations, runs and budgets are read for the guarded workspace only, as the routes answer them", async () => {
  const s = server();
  assert.deepEqual(await readJudgeRecords(dev, s.api), {
    configs: [CONFIG], runs: [RUN], budgets: [{ payer_ref: "payer-a", limit: BUDGET.limit, reserved: BUDGET.reserved, settled: BUDGET.settled }], more: false,
  });
  assert.deepEqual(s.sent.sort((a, b) => a.path.localeCompare(b.path)), [
    { path: "/lab/v1/judge/budgets", query: { provider_org_id: A } },
    { path: "/lab/v1/judge/configs", query: { provider_org_id: A, limit: "100" } },
    { path: "/lab/v1/judge/runs", query: { provider_org_id: A, limit: "100" } },
  ]);
  // An absent optional reads as null, as the schema says; a calibrated configuration keeps its interval.
  const unpriced = { ...RUN, reserved: undefined, settled: undefined }; // JSON drops both: absent on the wire
  const calibrated = { ...CONFIG, calibration: { state: "calibrated", labels: 25, required: 20, agreement: 0.82, interval: [0.7, 0.9] } };
  const read = await readJudgeRecords(dev, server({ configs: { body: { data: [calibrated] } }, runs: { body: { data: [unpriced] } } }).api);
  assert.deepEqual(read?.runs, [{ ...RUN, reserved: null, settled: null }]);
  assert.deepEqual(read?.configs, [calibrated]);
  const none = { body: { data: [] } };
  assert.deepEqual(await readJudgeRecords(dev, server({ configs: none, runs: none, budgets: none }).api), { configs: [], runs: [], budgets: [], more: false });
});

test("UX08-K02 a misconfigured Lab is unavailable before any call", async () => {
  assert.equal(await readJudgeRecords(dev, null), null);
});

test("UX08-K03 any refusal or lost answer of any one list is unavailable, never the other lists alone", async () => {
  for (const list of ["configs", "runs", "budgets"]) {
    for (const status of [401, 403, 404, 409, 422, 500, 503])
      assert.equal(await readJudgeRecords(dev, server({ [list]: { status, body: { error: { code: "x", message: "m", request_id: "q", retryable: false } } } }).api), null, `${list} ${status}`);
    assert.equal(await readJudgeRecords(dev, server({ [list]: { throws: true } }).api), null, `${list} transport`);
  }
});

test("UX08-K04 an answer it cannot read whole is unavailable: never CREDIT as PROVIDER_USD, never an unknown state, never a partial list", async () => {
  const bad: [string, Record<string, Answer>][] = [
    ["configs not a page", { configs: { body: [CONFIG] } }],
    ["budgets not a page", { budgets: { body: [BUDGET] } }],
    ["reserved in CREDIT", { runs: { body: { data: [{ ...RUN, reserved: { amount: "0.40000000", unit: "CREDIT" } }] } } }],
    ["settled in USD", { runs: { body: { data: [{ ...RUN, settled: { amount: "0.10000000", unit: "USD" } }] } } }],
    ["an inexact amount", { runs: { body: { data: [{ ...RUN, reserved: USD("0.4") }] } } }],
    ["a budget limit in CREDIT", { budgets: { body: { data: [{ ...BUDGET, limit: { amount: "5.00000000", unit: "CREDIT" } }] } } }],
    ["a budget without settled", { budgets: { body: { data: [{ ...BUDGET, settled: null }] } } }],
    ["an unknown run state", { runs: { body: { data: [{ ...RUN, domain_state: "estimated" }] } } }],
    ["an unknown calibration state", { configs: { body: { data: [{ ...CONFIG, calibration: { ...CAL, state: "scored" } }] } } }],
    ["a fractional count", { runs: { body: { data: [{ ...RUN, sent: 7.5 }] } } }],
    ["a three-point interval", { configs: { body: { data: [{ ...CONFIG, calibration: { ...CAL, state: "calibrated", interval: [0.1, 0.2, 0.3] } }] } } }],
    ["a text rubric version", { configs: { body: { data: [{ ...CONFIG, rubric_version: "2" }] } } }],
    ["a text cancel flag", { runs: { body: { data: [{ ...RUN, cancel_requested: "false" }] } } }],
    ["a second, broken row", { runs: { body: { data: [RUN, { ...RUN, run_id: 7 }] } } }],
  ];
  for (const [why, answers] of bad) assert.equal(await readJudgeRecords(dev, server(answers).api), null, why);
});

test("UX08-K05 a list with a further page is shown with more set, so the page never reads it as complete", async () => {
  for (const list of ["configs", "runs"]) {
    const read = await readJudgeRecords(dev, server({ [list]: { body: { ...(PAGES[list] as object), next_cursor: "c2" } } }).api);
    assert.equal(read?.more, true, list);
    assert.equal(read?.configs.length, 1);
  }
});
