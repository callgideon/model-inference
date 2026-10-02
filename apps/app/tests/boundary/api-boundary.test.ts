// AP-09 API-BOUNDARY (R271) for apps/app: the static inventory finds zero product database
// readers or writers, no identity-provider SDK (the auth-transport allowlist is empty), no service
// credential, no API-key minting and no credit arithmetic. Failure oracle: any such line added to
// the App's source names its file, line and rule here.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { ALLOWED, RULES, inventory, productFiles, scan } from "./inventory.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const report = (hits: { file: string; line: number; rule: string; text: string }[]) =>
  hits.map((h) => `${h.file}:${h.line} [${h.rule}] ${h.text}`).join("\n");

test("API-BOUNDARY: the App's source has no product database access, SDK, credential, key minting or money derivation", () => {
  const { findings } = inventory(appRoot);
  assert.deepEqual(findings, [], `\n${report(findings)}`);
});

test("API-BOUNDARY: each rule catches its own defect (the scanner is not vacuous)", () => {
  const samples: Record<string, string> = {
    "supabase-sdk": 'import { createServerClient } from "@supabase/ssr";',
    "table-read": 'await client.from("api_keys").select("id");',
    rpc: 'await client.rpc("claim_signup_grant", {});',
    "service-role": "const key = process.env.SUPABASE_SERVICE_ROLE_KEY;",
    "sql-driver": 'import { Pool } from "pg";',
    "sql-text": "const sql = `select * from public.consumer_jobs($1)`;",
    "key-minting": "const secret = generateKey();",
    "credit-arithmetic": "const spent = subCredit(creditsIn, wallet.ledgerTotal);",
    "cursor-signing": 'process.env["CONSOLE_CURSOR_SECRET"];',
  };
  assert.deepEqual(Object.keys(samples).sort(), RULES.map((r) => r.id).sort());
  for (const [rule, line] of Object.entries(samples)) {
    assert.ok(scan("sample.ts", line).some((hit) => hit.rule === rule), `rule ${rule} misses: ${line}`);
  }
  // Ordinary code is not a finding: Array.from/Buffer.from, a generated-client call, prose.
  const clean = 'Array.from(new Set(ids)); Buffer.from(text, "utf8"); await api.call("get", "/console/v1/keys"); // read from the API';
  assert.deepEqual(scan("clean.ts", clean), []);
});

test("API-BOUNDARY: every allowlisted exception is still needed and still display-only", () => {
  const { allowed } = inventory(appRoot);
  for (const entry of ALLOWED) {
    assert.ok(allowed.some((hit) => hit.rule === entry.rule && hit.file === entry.file), `stale allowance: ${entry.file} [${entry.rule}]`);
    assert.ok(entry.why.length > 20);
  }
});

test("API-BOUNDARY: the scan covers the product tree (pages, actions, lib, components, middleware)", () => {
  const files = productFiles(appRoot);
  for (const expected of ["app/actions.ts", "app/(console)/layout.tsx", "middleware.ts", "lib/session.ts", "components/sidebar.tsx"]) {
    assert.ok(files.includes(expected), `${expected} is not scanned`);
  }
  assert.ok(!files.some((f) => f.startsWith("tests/") || f.startsWith("lib/contracts/") || f.endsWith(".test.ts")));
});

test("API-BOUNDARY: the App's deploy environment holds no service credential and no cursor secret", () => {
  const env = readFileSync(join(appRoot, "lib/deploy/env.ts"), "utf8");
  assert.doesNotMatch(env, /SUPABASE_SERVICE_ROLE_KEY|CONSOLE_CURSOR_SECRET/);
  assert.match(env, /INFRX_API_BASE_URL/);
});
