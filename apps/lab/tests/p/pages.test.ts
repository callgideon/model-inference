// P4 pages, read as source (a .tsx page cannot run under node --test): records come from the pipelines
// port as the session's workspace; each idempotent form carries an id minted when it renders; paid forms
// show the USD budget and payer; no connector is chosen by the form; no page claims success on its own.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const ANNOTATIONS = "app/(provider)/annotations/page.tsx";
const TRAINING = "app/(provider)/training/page.tsx";
const PAGES = [ANNOTATIONS, TRAINING];

test("P4-P01 each page reads the pipeline records as the session's workspace and shows ?refused= only as fixed copy", () => {
  for (const path of PAGES) {
    const page = read(path);
    assert.match(page, /const workspace = await requireProviderWorkspace\(\);/, path);
    assert.match(page, /\.\w+\(workspace\b/, path); // LAB-08: the workspace itself is the actor
    assert.doesNotMatch(page, /providerId:/, path);
    assert.match(page, /const refused = refusalCopy\(query\.refused\);/, path);
    assert.doesNotMatch(page, /success|succeeded!|saved|done=|\.providerId\s*=/i, path);
  }
});

test("P4-P02 every import, export, bundle and checkpoint form carries an id minted at render, never typed or re-minted by the action", () => {
  const annotations = read(ANNOTATIONS);
  const training = read(TRAINING);
  assert.match(annotations, /<input type="hidden" name="importId" value=\{randomUUID\(\)\} \/>/);
  assert.match(annotations, /<input type="hidden" name="exportId" value=\{randomUUID\(\)\} \/>/);
  assert.match(training, /<input type="hidden" name="externalRunId" value=\{randomUUID\(\)\} \/>/);
  assert.match(training, /<input type="hidden" name="checkpointId" value=\{randomUUID\(\)\} \/>/);
  assert.doesNotMatch(read("lib/services/pipelines/actions.ts"), /randomUUID/);
});

test("P4-P03 the paid forms show the USD budget and named payer, and no form picks a connector", () => {
  const training = read(TRAINING);
  assert.match(training, /<input name="payerRef" required placeholder="lab:payer:…" \/><\/label>\s*<label>Limit, USD <input name="limitUsd" required/);
  assert.match(training, /Budget \(USD\)/);
  assert.match(training, /\{r\.budget\} · payer \{r\.payer\}/);
  assert.doesNotMatch(training, /name="connector"|CREDIT/);
  assert.match(training, /Automatic training connectors are not offered/);
});

test("P4-P04 labels show their kind and ground-truth status apart; the preview stand-in is labelled only when it is on", () => {
  const annotations = read(ANNOTATIONS);
  assert.match(annotations, /<td>\{l\.kind\}<\/td><td>\{l\.truth\}<\/td>/);
  for (const path of PAGES) assert.match(read(path), /\{isPreview\(\) && <PreviewNote records="pipeline" service="pipeline" \/>\}/, path);
});

test("P4-P05 the teacher section: a dry-run form with its batch id minted at render, its USD budget and payer; approval only where the view allows it; unavailable said as such", () => {
  const training = read(TRAINING);
  assert.match(training, /<form action=\{planTeachers\}>[\s\S]*<input type="hidden" name="batchId" value=\{randomUUID\(\)\} \/>/);
  assert.match(training, /<legend>Teacher budget \(USD\)<\/legend>/);
  assert.match(training, /<input name="payerRef" required placeholder="lab:payer:… \(pays the teacher\)" \/><\/label>\s*<label>Budget, USD <input name="budgetUsd" required/);
  assert.match(training, /<button type="submit">Plan a dry run \(nothing is sent\)<\/button>/);
  assert.match(training, /\{b\.approvable && \(\s*<form action=\{approveTeachers\}>\s*<input type="hidden" name="batchId" value=\{b\.id\} \/>/);
  assert.match(training, /\{b\.budget\}.*\{b\.ceiling\}/);
  assert.match(training, /!batches\.ok \? <p role="note">\{TEACHER_UNAVAILABLE\}<\/p>/);
  assert.doesNotMatch(training, /name="live"|name="approve"/);
});

test("P4-P06 each page shows the first failed read's fixed copy (common.ts firstFailure)", () => {
  assert.match(read(TRAINING), /if \(!runs\.ok \|\| !checkpoints\.ok\) return <p role="alert">\{REFUSAL_COPY\[firstFailure\(runs, checkpoints\)!\]\}<\/p>;/);
  assert.match(read(ANNOTATIONS), /const failed = firstFailure\(imports, exports, labels, disputes\);/);
  assert.match(read(ANNOTATIONS), /\{failed !== null \? \(\n\s+<p role="alert">\{REFUSAL_COPY\[failed\]\}<\/p>/);
});
