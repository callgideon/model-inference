// B4 pages, read as source (a .tsx page cannot run under node --test): records come from the
// evaluation port as the session's workspace, a refusal is only fixed copy, a form carries its own
// record id (a resubmit is the same launch), and nothing claims an outcome the records did not give.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const EVALS = "app/(provider)/evaluations/page.tsx";
const CHECKPOINTS = "app/(provider)/evaluations/checkpoints/page.tsx";
const EXPERIMENT = "app/(provider)/experiments/[id]/page.tsx";
const RUNS = "app/(provider)/evaluations/runs.tsx";
const PAGES = [EVALS, CHECKPOINTS, EXPERIMENT];

test("B4-P01 each page reads the evaluation records as the session's workspace and shows ?refused= only as fixed copy", () => {
  for (const path of PAGES) {
    const page = read(path);
    assert.match(page, /const workspace = await requireProviderWorkspace\(\);/, path);
    assert.match(page, /\.\w+\(workspace\b/, path); // LAB-08: the workspace itself is the actor
    assert.doesNotMatch(page, /providerId:/, path);
    assert.match(page, /return <p role="alert">\{REFUSAL_COPY\.unavailable\}<\/p>;/, path);
    assert.doesNotMatch(page, /searchParams\)\)\.(?!refused\))|\.providerId\s*=|formData/, path);
    assert.doesNotMatch(page, /success|succeeded|complete!|saved|improved!/i, path);
  }
  for (const path of [EVALS, CHECKPOINTS]) assert.match(read(path), /const refused = refusalCopy\(\(await searchParams\)\.refused\);/, path);
});

test("B4-P02 launch and subscribe forms are shown only to a role that runs evaluations and carry a fresh record id", () => {
  assert.match(read(EVALS), /\{holds\(workspace\.role, "run_evaluation"\) && \(\n\s+<form action=\{launchExperiment\}>/);
  assert.match(read(EVALS), /<input type="hidden" name="experiment_id" value=\{crypto\.randomUUID\(\)\} \/>/);
  assert.match(read(CHECKPOINTS), /\{holds\(workspace\.role, "run_evaluation"\) && \(\n\s+<form action=\{subscribeCheckpoints\}>/);
  assert.match(read(CHECKPOINTS), /<input type="hidden" name="subscription_id" value=\{crypto\.randomUUID\(\)\} \/>/);
  assert.match(read(RUNS), /\{r\.cancel && \(\n\s+<form action=\{cancelRun\}>/);
  assert.match(read(EVALS), /<select name="harness_ref" required>\{c\.harnesses\.map/, "H1 revisions are picked, never typed");
});

test("B4-P03 an experiment is found only among the workspace's own, compared only through B2's report, exported only once it exists", () => {
  const page = read(EXPERIMENT);
  assert.match(page, /const e = list\.value\.find\(\(x\) => x\.experiment_id === id\);\n\s+if \(e === undefined\) return <p role="alert">\{REFUSAL_COPY\.not_found\}<\/p>;/);
  assert.match(page, /const c = e\.report === null \? null : comparison\(e\.report\);/);
  assert.match(page, /\{c === null \? \(\n\s+<p>Pending: B2 compares the runs once both have ended\.<\/p>/);
  assert.equal(page.split("/report`").length - 1, 1, "one export link, inside the report branch");
  assert.ok(page.indexOf("/report`") > page.indexOf("{c === null ?"));
  assert.match(page, /<p role="status">\{c\.outcome\}<\/p>/);
});

test("B4-P04 the preview stand-in is labelled on every page only when it is on", () => {
  for (const path of PAGES) assert.match(read(path), /\{isPreview\(\) && <PreviewNote \/>\}/, path);
  assert.match(read(RUNS), /<p role="note">Preview: /);
});
