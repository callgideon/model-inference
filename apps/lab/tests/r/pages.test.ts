// R4 pages, read as source (a .tsx page cannot run under node --test): records come from the releases
// port as the session's workspace, a refusal is only fixed copy, no page claims success, and no page
// offers a shadow/canary launch or allocation control before P-12.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PAGES = ["releases", "optimizations"].map((p) => `app/(provider)/${p}/page.tsx`);

test("R4-P01 each page reads the records as the session's workspace and shows ?refused= only as fixed copy", () => {
  for (const path of PAGES) {
    const page = read(path);
    assert.match(page, /const workspace = await requireProviderWorkspace\(\);/, path);
    assert.match(page, /\.\w+\(workspace\b/, path); // LAB-08: the workspace itself is the actor
    assert.doesNotMatch(page, /providerId:/, path);
    assert.doesNotMatch(page, /searchParams\)\)\.(?!refused\))|\.providerId\s*=|formData/, path);
    // UX-10: a failed read is the section's own unavailable/denied state, never an empty page (tests/ux/releases UX10-P04, UX10-P07)
    assert.match(page, /\{!records\.ok \? \(|<Variants result=\{variants\} \/>/, path);
  }
  const releases = read(PAGES[0]);
  assert.match(releases, /const refused = refusalCopy\(\(await searchParams\)\.refused\);/);
  assert.match(releases, /<input type="hidden" name="fence" value=\{r\.fence\} \/>/, "a proposal names the revision the page showed");
});

test("R4-P02 no page offers a launch or allocation control or claims success; the preview stand-in is labelled only when it is on", () => {
  for (const path of PAGES) {
    const page = read(path);
    assert.doesNotMatch(page, /success|succeeded|rolled back!|saved/i, path);
    assert.doesNotMatch(page, /name="(mode|cohort|weight\w*|budget|candidate\w*|endpoint\w*)"/, path);
    assert.match(page, new RegExp(`\\{isPreview\\(\\) && <PreviewNote records="${path.includes("releases") ? "release" : "variant"}" service="rollout" \\/>\\}`), path);
  }
  assert.equal([...read(PAGES[0]).matchAll(/<form /g)].length, 1, "one form: the expand/rollback proposal");
  assert.doesNotMatch(read(PAGES[1]), /<form /);
});
