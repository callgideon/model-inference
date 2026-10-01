// V2 page and panels, read as source (a .tsx file cannot run under node --test): the session's
// workspace is the only actor, feedback does not wait on the projection, content is stated and never
// read or offered (WR-V2-2 pending), and every control is a native link (keyboard) in a layout that wraps.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PAGE = "app/(provider)/requests/[id]/page.tsx";
const PANELS = "components/traces/detail/panels.tsx";

test("V2-P01 the page reads as the session's workspace, and feedback shows whatever the trace read says", () => {
  const page = read(PAGE);
  assert.match(page, /const workspace = await requireProviderWorkspace\(\);/);
  assert.match(page, /const actor = \{ providerId: workspace\.providerId, role: workspace\.role \};/);
  assert.match(page, /Promise\.all\(\[traces\.detail\(actor, id\), reviewRequestFeedback\(id\), /);
  assert.match(page, /\n      <FeedbackPanel result=\{feedback\} \/>\n/, "the feedback panel is not inside a trace branch");
  assert.match(page, /\{!trace\.ok && <p role="alert">\{TRACE_COPY\[trace\.reason\]\}<\/p>\}/);
  assert.doesNotMatch(page, /providerId:\s*(id|params|searchParams)|formData/);
});

test("V2-P02 content is never read or offered: the panel states the record's content state, and ?content= is ignored", () => {
  const page = read(PAGE);
  const panels = read(PANELS);
  assert.match(page, /\{trace\.ok && <ContentPanel detail=\{trace\.value\} \/>\}/);
  assert.doesNotMatch(page, /searchParams|content=1|contentView/);
  assert.match(panels, /<p role="status">\{CONTENT_COPY\[contentState\(detail\)\]\}<\/p>/);
  assert.doesNotMatch(panels, /Show content|href|<pre/);
  assert.match(panels, /const WRAP = \{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" \} as const;/);
  for (const source of [page, panels]) assert.doesNotMatch(source, /dangerouslySetInnerHTML|innerHTML|onClick|tabIndex|<table/);
  assert.match(read("app/(provider)/requests/[id]/loading.tsx"), /role="status"/);
});

test("V2-P03 each feedback row shows its provenance (who) beside what and when", () => {
  assert.match(read(PANELS), /<strong>\{r\.what\}<\/strong> · \{r\.who\} · \{r\.when\}/);
});
