// WR-C3L-2 CONSOLE-FLOWS: the Lab judge page posts C3L's four actions. The run id is minted once per
// page render (so a double-click repeats it and the RPC answers the same run, C3L-B02) and never by the
// action or the browser; the provider never rides a form field; a viewer gets no form and only an
// administrator the budget form. The page is a server component, so its source is the unit under test
// (as V3-P01 reads panel.tsx); the outcome copy is a pure module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { outcomeText } from "../../../lib/services/judge/copy.ts";

const lab = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PAGE = "app/(provider)/judge/page.tsx";
const FORM = "app/(provider)/judge/form.tsx";

test("C3P-01 the page runs the guard, then mints one run id per render into the run form only", () => {
  const page = read(PAGE);
  const guard = page.indexOf("await requireProviderWorkspace()");
  const mint = page.indexOf("crypto.randomUUID()");
  assert.ok(guard !== -1 && mint > guard, "the guard runs before anything is rendered");
  assert.equal(page.split("crypto.randomUUID()").length - 1, 1);
  for (const path of [FORM, "lib/services/judge/actions.ts", "lib/services/judge/core.ts"]) {
    assert.equal(read(path).includes("randomUUID"), false, `${path} never mints a run id`);
  }
  assert.equal(page.split("hidden={{ run_id: runId }}").length - 1, 1);
  assert.match(page, /action=\{requestJudgeRun\}[^>]*hidden=\{\{ run_id: runId \}\}/);
});

test("C3P-02 the four forms post the four C3L actions and never a provider or identity field", () => {
  const page = read(PAGE);
  for (const action of ["configureJudge", "setJudgeBudget", "requestJudgeRun", "judgeCalibrationPage"]) {
    assert.equal(page.split(`action={${action}}`).length - 1, 1, action);
  }
  const names = [...page.matchAll(/name: "([a-z_]+)"/g)].map((m) => m[1]);
  assert.equal(/provider|user|role/.test(names.join(" ")), false);
  assert.deepEqual(names.sort(), ["after", "config_id", "grantor_org_id", "judge_model", "limit", "limit_usd", "model_id", "payer_ref", "payer_ref", "rubric_version", "sample_size"]);
  assert.match(read(FORM), /<input type="hidden" key=\{name\} name=\{name\} value=\{value\} \/>/);
});

test("C3P-03 a viewer gets no judge form; only an administrator gets the budget form", () => {
  const page = read(PAGE);
  const viewer = page.indexOf('if (workspace.role === "viewer") return');
  assert.ok(viewer !== -1 && viewer < page.indexOf("<JudgeForm"), "the viewer answer comes before any form");
  assert.match(page, /workspace\.role === "administrator" && \(\s*<JudgeForm\s+action=\{setJudgeBudget\}/);
  assert.match(page, /PROVIDER_USD/);
});

test("C3P-04 every outcome has fixed copy, and a refusal never renders data", () => {
  assert.equal(outcomeText({ ok: false, reason: "denied" }), "Refused: this workspace may not do that.");
  assert.equal(outcomeText({ ok: false, reason: "invalid" }), "Check the fields: something is not in the expected form.");
  assert.equal(outcomeText({ ok: false, reason: "conflict" }), "Already submitted with different values. Reload the page and try again.");
  assert.equal(outcomeText({ ok: false, reason: "unavailable" }), "The judge service is unavailable. Nothing was changed.");
  assert.equal(outcomeText({ ok: true, data: { config_id: "c", state: "insufficient", labels: 3, required: 30, agreement: null, interval: null } }),
    "Calibration: insufficient, 3 of 30 reviewed labels.");
  assert.equal(outcomeText({ ok: true, data: { config_id: "c", state: "calibrated", labels: "x", required: 30 } }), "Done.");
  assert.equal(outcomeText({ ok: true, data: { config_id: "c", state: "scored", labels: 3, required: 30 } }), "Done.");
  assert.equal(outcomeText({ ok: true, data: { operation_id: "r", state: "queued" } }), "Done.");
  assert.match(read(FORM), /\{state && <p role="status">\{outcomeText\(state\)\}<\/p>\}/);
});
