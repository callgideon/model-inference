// UX-08 (L-08 judge setup, AP-08 types): the judge records' view model against the committed Lab
// OpenAPI (apps/infrx-api/openapi/lab-control.json), and the judge page's place under Evaluations.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import { budgetRow, calibration, configRow, RUN_STATE, runRow, type Config, type JudgeRun } from "../../../app/(provider)/judge/view.ts";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const schemas = JSON.parse(read("../infrx-api/openapi/lab-control.json")).components.schemas;
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
  assert.match(page, /<JudgeRecords records=\{null\} \/>/);
  const records = read("app/(provider)/judge/records.tsx");
  assert.match(records, /if \(records === null\)\s+return <ServiceState state="unavailable"/);
  assert.match(page, /not ground truth/);
});
