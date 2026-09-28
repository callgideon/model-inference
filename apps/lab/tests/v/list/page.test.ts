// V1M page and list markup, read as source (a .tsx file cannot run under node --test): the session's
// workspace is the only actor, the read is the provider trace port, every row is a native link to V2's
// detail page (keyboard), the layout wraps (mobile), and nothing comes from the App or its UI kit.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const lab = resolve(import.meta.dirname, "../../..");
const read = (path: string) => readFileSync(join(lab, path), "utf8");
const PAGE = "app/(provider)/requests/page.tsx";
const TABLE = "components/traces/list/trace-table.tsx";
const ERROR = "app/(provider)/requests/error.tsx";

test("V1M-P01 the list reads as the session's workspace through the Lab trace service", () => {
  const page = read(PAGE);
  assert.match(page, /const workspace = await requireProviderWorkspace\(\);\n  const actor = \{ providerId: workspace\.providerId, role: workspace\.role \};/);
  assert.match(page, /const params = parseListParams\(await searchParams\);/);
  assert.match(page, /buildListView\(await labTraces\(\)\.list\(actor, params\.cursor\), params\.cursor\)/);
  assert.match(page, /<RejectedParams rejected=\{params\.rejected\} ignored=\{params\.ignored\} \/>/);
  assert.doesNotMatch(page, /providerId:\s*(params|searchParams)|getSession|providerRoute|consoleContext/);
});

test("V1M-P02 rows are native links to the request page; states and refusals are text; no App kit, no scripts on rows", () => {
  const table = read(TABLE);
  assert.match(table, /<a href=\{r\.href\}>\{r\.requestId\}<\/a>/);
  assert.match(table, /\{r\.started\} · \{r\.duration\} · \{r\.model\} · \{r\.content\}/);
  assert.match(table, /view\.nextHref !== null && <a href=\{view\.nextHref\}>Older requests<\/a>/);
  assert.match(table, /view\.firstHref !== null && <a href=\{view\.firstHref\}>Newest requests<\/a>/);
  assert.match(table, /<p role="alert">\{view\.message\}<\/p>/);
  assert.match(table, /<p role="status">\{view\.message\}<\/p>/);
  assert.match(table, /overflowWrap: "anywhere"/);
  for (const path of [PAGE, TABLE, ERROR]) {
    const source = read(path);
    assert.doesNotMatch(source, /@\/components\/ui|components\/page-header|lib\/format|lib\/deploy|apps\/app|dangerouslySetInnerHTML|tabIndex/, path);
  }
  assert.doesNotMatch(table, /onClick/);
  assert.match(read(ERROR), /"use client";[\s\S]*<Link href="\/requests">Start from the newest requests<\/Link>/);
});
