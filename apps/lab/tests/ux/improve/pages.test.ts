// UX-06/UX-09 server pages, read as source (a .tsx server page cannot run under node --test; the P4 and
// L1 suites read pages the same way): every state keeps the page's identity, a failed read is never an
// empty list, writes are offered only to a writer and only on records the page could read, and ref
// fields offer the workspace's dataset catalog. Each case names what it catches.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const D = "app/(provider)/datasets";
const TRAINING = "app/(provider)/training/page.tsx";
const ANNOTATIONS = "app/(provider)/annotations/page.tsx";
/** The source between `from` and `to` (exclusive). */
const between = (source: string, from: string, to: string) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));

test("UX06-P01 the dataset library keeps its header in every state: a failed read is unavailable or denied, empty only for an answered empty list", () => {
  const page = read(`${D}/page.tsx`);
  const header = page.indexOf("<PageHeader");
  assert.ok(header > 0 && header < page.indexOf("{!versions.ok ? ("), "the header renders before every branch");
  assert.match(page, /\{!versions\.ok \? \(\s*<ServiceState\s+state=\{versions\.error === "denied" \? "denied" : "unavailable"\}/);
  assert.match(page, /\) : versions\.value\.length === 0 \? \(\s*<ServiceState state="empty"/);
  assert.match(page, /action=\{<Link className=\{buttonClass\(\)\} href="\/datasets">Try again<\/Link>\}/, "a failed read offers a retry");
});

test("UX06-P02 only a writer is offered the import, with a spec template whose import and dataset ids are minted at render for this workspace", () => {
  const page = read(`${D}/page.tsx`);
  assert.match(page, /const writer = holds\(workspace\.role, "run_evaluation"\);/);
  assert.match(page, /\{writer \? \(\s*<section id="import"/);
  assert.match(page, /actions=\{writer \? <a className=\{buttonClass\("primary"\)\} href="#import">Import dataset<\/a> : undefined\}/);
  assert.match(page, /template=\{importTemplate\(\{ providerId: workspace\.providerId, importId: randomUUID\(\), datasetId: randomUUID\(\), createdAt: new Date\(\)\.toISOString\(\) \}\)\}/);
  assert.match(page, /<ImportWizard\s+preview=\{previewImportAction\}\s+start=\{startImportAction\}/, "the real server actions, never the harness's");
});

test("UX06-P03 a version or import that cannot be read keeps its header and says so; derive and export only for a writer; a published import is not a release", () => {
  const version = read(`${D}/[ref]/page.tsx`);
  assert.match(version, /if \(!status\.ok\) \{\s*return \(\s*<div className="lab-stack">\s*<PageHeader breadcrumb=\{CRUMBS\} title="Dataset version" \/>\s*<ServiceState/);
  assert.match(version, /\{holds\(workspace\.role, "run_evaluation"\) && \(/);
  assert.match(version, /<DeriveForm base=\{ref\} datasetId=\{randomUUID\(\)\} derive=\{deriveAction\} \/>/, "a new dataset id per derive, minted at render");
  const job = read(`${D}/imports/[id]/page.tsx`);
  assert.match(job, /if \(!job\.ok\) \{\s*return \(\s*<div className="lab-stack">\s*<PageHeader breadcrumb=\{CRUMBS\} title="Dataset import" \/>\s*<ServiceState/);
  assert.match(job, /const TONE = \{ success: "success", progress: "info", error: "danger" \} as const;/, "only a published import reads as a proven outcome");
  assert.match(job, /\{view\.tone === "success" && <p className=\{styles\.note\}>Published means a new private dataset version exists in this workspace\. It is not a public release\.<\/p>\}/);
});

test("UX09-P01 training: the run records sit behind their own gate; the heading and the teacher section stay, and no bundle is offered on records the page cannot read", () => {
  const page = read(TRAINING);
  const records = between(page, "async function Records(", "export default async function Training(");
  const main = page.slice(page.indexOf("export default async function Training("));
  assert.match(records, /if \(!runs\.ok \|\| !checkpoints\.ok\) return <p role="alert">/, "the records gate is the records section's");
  assert.doesNotMatch(records, /Teacher labelling|prepareTraining|planTeachers/, "nothing else hides behind it");
  assert.ok(main.indexOf("<h1>Training</h1>") < main.indexOf("<Records workspace={workspace} runs={runs} checkpoints={checkpoints} />"), "the heading renders first");
  assert.ok(main.indexOf("<Records ") < main.indexOf('<h2 id="teacher">Teacher labelling</h2>'), "the teacher section follows, outside the gate");
  assert.match(main, /const gated = firstFailure\(runs, checkpoints\) !== null;/);
  assert.match(main, /\{gated && <p role="note">\{GATED_COPY\}<\/p>\}/);
  assert.match(main, /\{!gated && \(\s*<form action=\{prepareTraining\}>/, "no bundle on unread records");
  assert.match(main, /\{runs\.ok && runs\.value\.length > 0 && \(\s*<form action=\{importCheckpoint\}>/);
});

test("UX09-P02 an unknown submission is offered the lookup form - its own run, the lookup-only op - and a writer's ref fields offer the dataset catalog", () => {
  const page = read(TRAINING);
  assert.match(page, /const writer = holds\(workspace\.role, "run_evaluation"\);\s*return \(/, "the records' writer is the session's role");
  assert.match(page, /\{canLookUp\(runs\.value\[i\]\.state, writer\) && \(\s*<form action=\{runAction\}>\s*<input type="hidden" name="externalRunId" value=\{r\.id\} \/>\s*<input type="hidden" name="op" value="submit" \/>\s*<button type="submit">\{LOOKUP_LABEL\}<\/button>/);
  assert.equal(page.match(/list="dataset-versions"/g)?.length, 2, "both dataset ref fields");
  assert.match(page, /writer \? \(await datasetsPort\(\)\)\.versions\(workspace\.providerId\) : null,/, "only a writer's page reads the catalog");
  assert.match(page, /<DatasetOptions id="dataset-versions" versions=\{versions\} \/>/);
});

test("UX09-P03 review: the page keeps its header in every state, offers the dataset catalog to open, and the reviewer id is labelled as the member-list fallback", () => {
  const page = read(ANNOTATIONS);
  const header = page.indexOf("<PageHeader");
  assert.ok(header > 0 && header < page.indexOf("{failed !== null ? ("), "the header renders before the failure branch");
  assert.match(page, /<PageHeader\s+title="Review"/);
  assert.match(page, /<input name="dataset" required list="dataset-versions"/);
  assert.match(page, /<DatasetOptions id="dataset-versions" versions=\{versions\} \/>/);
  assert.match(page, /Reviewer \(member user id\)/);
  assert.match(page, /No member list is read here yet: enter the member&apos;s user id. The service refuses anyone who is not a current member allowed to review./);
});
