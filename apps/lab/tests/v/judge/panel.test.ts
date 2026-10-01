// V3 panel and its place on the request page, read as source (a .tsx file cannot run under node
// --test): read only (no paid-submit control anywhere), fixed copy for a refusal, consent linked to
// Settings, and the judge read as the session's workspace only beside a request the trace read shows.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PANEL = "components/traces/judge/panel.tsx";
const PAGE = "app/(provider)/requests/[id]/page.tsx";

test("V3-P01 the judge panel is read only, shows refusals as fixed copy and links judge consent to Settings", () => {
  const panel = read(PANEL);
  assert.doesNotMatch(panel, /<form|<button|action=|onClick|dangerouslySetInnerHTML|tabIndex/);
  assert.match(panel, /<p role="status">\{JUDGE_COPY\[result\.reason\]\}<\/p>/);
  assert.match(panel, /const rows = result\.ok \? judgeRows\(result\.value\) : \[\];/);
  assert.match(panel, /<Link href="\/settings">Settings<\/Link>/);
  assert.match(panel, /<p>Verdict: \{r\.verdict\}<\/p>/, "the verdict is the view's, never a literal");
  assert.match(panel, /<p>\{r\.calibration\}<\/p>/, "calibration is the view's, never a literal");
});

test("V3-P02 the page reads judge runs as the session's actor and shows them only beside a visible request", () => {
  const page = read(PAGE);
  assert.match(page, /judgePort\(\)\.runs\(workspace, id\)\]\);/); // LAB-08: the workspace itself is the actor
  assert.match(page, /\n      \{trace\.ok && <JudgePanel result=\{judge\} \/>\}\n/);
});
