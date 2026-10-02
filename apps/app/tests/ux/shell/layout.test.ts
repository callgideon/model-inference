// WR-UXF-2 (UX-02): the production console layout gives its ready-state main the skip link's target, as
// the harness layout does (UXN-05 proves the skip link lands on it there).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("WR-UXF-2 the console layout's main is the skip link's target (id MAIN_ID, focusable by script)", () => {
  assert.match(readFileSync("app/(console)/layout.tsx", "utf8"), /<main id=\{MAIN_ID\} tabIndex=\{-1\}/);
});
