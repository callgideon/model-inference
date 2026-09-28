// L4 pages, read as source (a .tsx page cannot run under node --test): records come from the control
// port as the session's workspace, a refusal is only ever fixed copy, and no page claims success.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PAGES = ["overview", "models", "deployments", "settings"].map((p) => `app/(provider)/${p}/page.tsx`);

test("L4-P01 each page reads the control records as the session's workspace and shows ?refused= only as fixed copy", () => {
  for (const path of PAGES.filter((p) => !p.includes("settings"))) {
    const page = read(path);
    assert.match(page, /const workspace = await requireProviderWorkspace\(\);/, path);
    assert.match(page, /\{ providerId: workspace\.providerId, role: workspace\.role \}/, path);
    assert.doesNotMatch(page, /searchParams\)\)\.(?!refused\))|\.providerId\s*=|formData/, path);
  }
  for (const path of ["app/(provider)/models/page.tsx", "app/(provider)/deployments/page.tsx"])
    assert.match(read(path), /const refused = refusalCopy\(\(await searchParams\)\.refused\);/, path);
  for (const path of PAGES) assert.doesNotMatch(read(path), /success|succeeded|published!|saved/i, path);
});

test("L4-P02 the provider layout links the L4 pages and labels the preview stand-in only when it is on", () => {
  const layout = read("app/(provider)/layout.tsx");
  for (const href of ["/overview", "/models", "/deployments", "/judge", "/evaluations", "/releases", "/optimizations", "/annotations", "/training", "/datasets", "/settings"]) assert.ok(layout.includes(`href="${href}"`), href);
  assert.match(layout, /\{isPreview\(\) && <p role="note">Preview: /);
});
