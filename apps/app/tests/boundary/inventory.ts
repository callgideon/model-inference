/**
 * AP-09 API-BOUNDARY (R271): the static inventory of what the consumer App may not do itself.
 *
 * Every product read and write goes through infrx-api (`@infrx/api-client` via `lib/api/`), so the
 * App's own source holds no database reader or writer, no identity-provider SDK, no service
 * credential, no API-key minting and no money derivation. Scanned: every `.ts`/`.tsx`/`.mjs` under
 * the App except `node_modules`, `.next`, the tests (`tests/`, `*.test.ts`) and `lib/contracts/`
 * (the coordinator's frozen contract types, money helpers and their in-memory conformance fake;
 * none of it runs I/O). Each rule is one regular expression over a file's text; a finding names the
 * file, the line and the rule.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

export type Rule = { id: string; what: string; pattern: RegExp };

export const RULES: readonly Rule[] = [
  { id: "supabase-sdk", what: "an identity-provider or database SDK import (the auth-transport allowlist is empty)", pattern: /from\s+["']@supabase\/|import\(\s*["']@supabase\/|require\(\s*["']@supabase\// },
  { id: "table-read", what: "a PostgREST table read or write (.from(\"relation\"))", pattern: /\.from\(\s*["'`][a-z_][a-z0-9_]*["'`]\s*\)/ },
  { id: "rpc", what: "a database function call (.rpc(...))", pattern: /\.rpc\(/ },
  { id: "service-role", what: "the service-role credential", pattern: /SUPABASE_SERVICE_ROLE_KEY|service_role|createAdminClient/ },
  { id: "sql-driver", what: "a SQL driver import", pattern: /from\s+["'](pg|postgres|@neondatabase\/[^"']*|kysely|drizzle-orm[^"']*|mysql2?)["']/ },
  { id: "sql-text", what: "SQL text against the product schemas", pattern: /\b(from|into|update|join)\s+(public|infrx|auth)\.[a-z_]+|\b(select|from|where)\s+\$\{/i },
  { id: "key-minting", what: "API-key generation or hashing (the API mints keys)", pattern: /\b(generateKey|hashKey|keyPrefix)\s*\(|key_hash|lib\/keys/ },
  { id: "credit-arithmetic", what: "money derivation from ledger rows (the API states balances and spend)", pattern: /\b(addCredit|subCredit|totalCredit|availableCredit|addMoney|subMoney|addUsd|subUsd|spentCredit|creditsIn)\b/ },
  { id: "cursor-signing", what: "a console cursor secret (cursors are the API's, opaque)", pattern: /CONSOLE_CURSOR_SECRET/ },
];

/**
 * Display-only exceptions, each with its reason. Nothing here reads or writes product state.
 * `rule` + `file` must both match; a stale entry (the file no longer trips the rule) fails a test.
 */
export const ALLOWED: readonly { rule: string; file: string; why: string }[] = [
  {
    rule: "credit-arithmetic",
    file: "app/(console)/billing/credit-view-model.ts",
    why: "walletReconciles compares the API's own three figures (available = balance - reserved) to flag a mismatch; it never shows a derived figure",
  },
];

const SKIP_DIRS = new Set(["node_modules", ".next", "tests", ".git"]);
const SOURCE = /\.(ts|tsx|mjs)$/;

export function productFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      const rel = relative(root, path).split(sep).join("/");
      if (statSync(path).isDirectory()) {
        if (!SKIP_DIRS.has(name) && rel !== "lib/contracts") walk(path);
      } else if (SOURCE.test(name) && !/\.test\.ts$/.test(name) && !name.endsWith(".d.ts")) {
        out.push(rel);
      }
    }
  };
  walk(root);
  return out.sort();
}

export type Finding = { file: string; line: number; rule: string; text: string };

/** Every rule hit in one file's text, before the allowlist. */
export function scan(file: string, text: string, rules: readonly Rule[] = RULES): Finding[] {
  const found: Finding[] = [];
  text.split("\n").forEach((line, index) => {
    for (const rule of rules) {
      if (rule.pattern.test(line)) found.push({ file, line: index + 1, rule: rule.id, text: line.trim().slice(0, 160) });
    }
  });
  return found;
}

export function inventory(root: string): { findings: Finding[]; allowed: Finding[] } {
  const findings: Finding[] = [];
  const allowed: Finding[] = [];
  for (const file of productFiles(root)) {
    for (const hit of scan(file, readFileSync(join(root, file), "utf8"))) {
      (ALLOWED.some((a) => a.rule === hit.rule && a.file === hit.file) ? allowed : findings).push(hit);
    }
  }
  return { findings, allowed };
}
