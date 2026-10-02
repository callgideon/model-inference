// UX-08 (L-08 judge setup, AP-08 types): the judge records' view model against the committed Lab
// OpenAPI (apps/infrx-api/openapi/lab-control.json), and the judge page's place under Evaluations.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import { budgetRow, calibration, configRow, retryKeepsKey, RUN_STATE, runRow, type Config, type JudgeRun } from "../../../app/(provider)/judge/view.ts";
import type { LabApi } from "../../../lib/api/index.ts";
import type { Membership } from "../../../lib/auth/access.ts";
import { calibration as calibrate, configure, requestRun, setBudget, type Outcome } from "../../../lib/services/judge/core.ts";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
// The OpenAPI the Lab client is generated from, found through the linked package (a mutant copy of the
// Lab has no ../infrx-api beside it).
const openapi = new URL("../../../apps/infrx-api/openapi/lab-control.json", import.meta.resolve("@infrx/api-client/lab"));
const schemas = JSON.parse(readFileSync(openapi, "utf8")).components.schemas;
const PAGE = "app/(provider)/judge/page.tsx";

const RUN: JudgeRun = {
  run_id: "r1", config_id: "c1", domain_state: "submitted", cancel_requested: false, payer_ref: "payer-a",
  sample_size: 10, selected: 8, sent: 8, accepted: 7, rejected: 1, requested_at: "2026-10-02T00:00:00Z",
  reserved: { amount: "0.40000000", unit: "PROVIDER_USD" }, settled: null,
};
const CONFIG: Config = {
  config_id: "c1", model_id: "m1", judge_model: "judge-x", rubric_version: 2, sample_size: 10, grantor_org_id: "g1", created_at: "2026-10-01T00:00:00Z",
  calibration: { state: "insufficient", labels: 3, required: 20, agreement: null, interval: null },
};

test("UX08-J01 the view reads only fields the committed judge API documents, and names every run state it can return", () => {
  const props = (name: string) => Object.keys(schemas[name].properties).sort();
  for (const k of Object.keys(RUN)) assert.ok(props("RunDoc").includes(k), `RunDoc.${k}`);
  for (const k of Object.keys(CONFIG)) assert.ok(props("ConfigDoc").includes(k), `ConfigDoc.${k}`);
  for (const k of Object.keys(CONFIG.calibration)) assert.ok(props("CalibrationDoc").includes(k), `CalibrationDoc.${k}`);
  for (const k of ["payer_ref", "limit", "reserved", "settled"]) assert.ok(props("BudgetDoc").includes(k), `BudgetDoc.${k}`);
  assert.deepEqual(Object.keys(RUN_STATE).sort(), [...schemas.RunDoc.properties.domain_state.enum].sort());
});

test("UX08-J02 calibrated is said only for a calibrated state; too few reference labels says how many of how many", () => {
  assert.equal(calibration(CONFIG.calibration), "not calibrated: 3 of 20 reference labels");
  assert.equal(calibration({ ...CONFIG.calibration, state: "uncalibrated", labels: 25 }), "not calibrated: 25 of 20 reference labels");
  assert.equal(calibration({ state: "calibrated", labels: 25, required: 20, agreement: 0.82, interval: [0.7, 0.9] }), "calibrated: agreement 0.82 [0.7, 0.9] over 25 reference labels");
  assert.equal(configRow(CONFIG).calibration, "not calibrated: 3 of 20 reference labels");
});

test("UX08-J03 a judge run shows the backend's state and counts; an ambiguous send is unknown, never retried or failed", () => {
  const row = runRow(RUN);
  assert.equal(row.state, "sent, awaiting the judge");
  assert.equal(row.counts, "8 of 8 selected sent · 7 accepted · 1 rejected");
  assert.deepEqual(row.money, ["reserved 0.40000000 PROVIDER_USD", "settled none"]);
  assert.equal(runRow({ ...RUN, domain_state: "ambiguous" }).state, "outcome unknown: not resent automatically");
  assert.equal(runRow({ ...RUN, cancel_requested: true }).state, "sent, awaiting the judge · cancel requested");
  assert.equal(runRow({ ...RUN, domain_state: "cancelled", cancel_requested: true }).state, "cancelled");
});

test("UX08-J04 a budget shows limit, reserved and settled each with its own unit, never a sum", () => {
  const b = budgetRow({ payer_ref: "payer-a", limit: { amount: "5.00000000", unit: "PROVIDER_USD" }, reserved: { amount: "0.40000000", unit: "PROVIDER_USD" }, settled: { amount: "0.00000000", unit: "PROVIDER_USD" } });
  assert.deepEqual(b, { payer: "payer-a", limit: "5.00000000 PROVIDER_USD", reserved: "0.40000000 PROVIDER_USD", settled: "0.00000000 PROVIDER_USD" });
});

test("UX08-J05 judge setup sits under Evaluations and says unreadable records are unavailable, not absent", () => {
  const page = read(PAGE);
  assert.match(page, /<PageHeader\s+title="Judge setup"\s+breadcrumb=\{\[\{ href: "\/evaluations", label: "Evaluations" \}\]\}/);
  // WR-UX08-3: the records come from the judge API's list reads (records.test.ts), null on any refusal.
  assert.match(page, /const records = await readJudgeRecords\(workspace\);/);
  assert.match(page, /<JudgeRecords records=\{records\} \/>/);
  const records = read("app/(provider)/judge/records.tsx");
  assert.match(records, /if \(records === null\)\s+return <ServiceState state="unavailable"/);
  assert.match(records, /\{records\.more && <p>More configurations or runs exist than are shown here/);
  assert.match(page, /not ground truth/);
});

// Register row 98: the forms send what their actions read. A page's JudgeForm block, by its action.
const form = (page: string, action: string) => {
  const at = page.indexOf(`action={${action}}`);
  return page.slice(at, page.indexOf("/>", at));
};
const names = (block: string) => [...block.matchAll(/name: "([a-z_]+)"|hidden=\{\{ ([a-z_]+):/g)].map((m) => m[1] ?? m[2]).sort();

test("UX08-J06 configure and budget send a key minted at render; only an unknown outcome keeps it for the retry, a definite answer frees it", () => {
  assert.equal(retryKeepsKey({ ok: false, reason: "unavailable" }), true);
  for (const o of [{ ok: true, data: {} }, { ok: false, reason: "denied" }, { ok: false, reason: "invalid" }, { ok: false, reason: "conflict" }] as Outcome[])
    assert.equal(retryKeepsKey(o), false, JSON.stringify(o));
  const page = read(PAGE);
  for (const action of ["configureJudge", "setJudgeBudget"])
    assert.match(form(page, action), /hidden=\{\{ idempotency_key: mint\(\) \}\}\s+keyed="idempotency_key"\s*$/, action);
  assert.equal(/keyed=/.test(form(page, "requestJudgeRun") + form(page, "judgeCalibrationPage")), false, "the run keeps C3L's per-render run id; a read has no key");
  const f = read("app/(provider)/judge/form.tsx");
  assert.match(f, /if \(keyed && !retryKeepsKey\(outcome\)\) setValues\(\(v\) => \(\{ \.\.\.v, \[keyed\]: crypto\.randomUUID\(\) \}\)\);/);
  assert.match(f, /Object\.entries\(values\)\.map\(/);
});

test("UX08-J07 each judge form carries every field its action reads, so a filled form is sent, never refused as invalid", async () => {
  const page = read(PAGE);
  assert.deepEqual(names(form(page, "configureJudge")), ["grantor_org_id", "idempotency_key", "judge_model", "model_id", "rubric_version", "sample_size"]);
  assert.deepEqual(names(form(page, "setJudgeBudget")), ["idempotency_key", "limit_usd", "payer_ref"]);
  assert.deepEqual(names(form(page, "requestJudgeRun")), ["config_id", "payer_ref", "run_id"]);
  assert.deepEqual(names(form(page, "judgeCalibrationPage")), ["config_id"]);
  // The filled forms through the actions' core, on a port that answers every call.
  const P = "a0000000-0000-4000-8000-00000000000a";
  const U = "b0000000-0000-4000-8000-00000000000b";
  const filled: Record<string, string> = {
    grantor_org_id: U, model_id: U, judge_model: "claude-opus-5", rubric_version: "1", sample_size: "50", idempotency_key: U,
    payer_ref: `lab:payer:${P}:${U}@sha256:${"c".repeat(64)}`, limit_usd: "10.00000000", config_id: U, run_id: U,
  };
  const sent: unknown[] = [];
  const api = { call: async (...args: unknown[]) => (sent.push(args), { ok: true, status: 200, data: {}, requestId: "q", location: null }) } as unknown as LabApi;
  const admin: Membership = { providerId: P, providerName: "P", role: "administrator", capabilities: ["run_evaluation", "manage_members"] };
  const input = (action: string) => Object.fromEntries(names(form(page, action)).map((n) => [n, filled[n]]));
  assert.equal((await configure(api, admin, input("configureJudge"))).ok, true);
  assert.equal((await setBudget(api, admin, input("setJudgeBudget"))).ok, true);
  assert.equal((await requestRun(api, admin, input("requestJudgeRun"))).ok, true);
  assert.equal((await calibrate(api, admin, input("judgeCalibrationPage"))).ok, true);
  assert.equal(sent.length, 4);
});
