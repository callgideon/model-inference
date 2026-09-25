// WR-1/WR-2 composed check (coordinator adds it next to the patch, e.g. tests/u/sidebar-credits.test.ts).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("U1R-WR12 the sidebar shows the individual's CREDIT wallet, labelled Credits, never the org's USD balance", () => {
  const layout = readFileSync("app/(console)/layout.tsx", "utf8");
  assert.match(layout, /sidebarCredits\(await \(await consumerCreditReads\(\)\)\.reads\.wallet\(\)\)/);
  assert.doesNotMatch(layout, /getBalance|sidebarBalance/);
  const sidebar = readFileSync("components/sidebar.tsx", "utf8");
  assert.match(sidebar, /href: "\/billing", label: "Credits"/);
  assert.doesNotMatch(sidebar, />Balance</);
});
