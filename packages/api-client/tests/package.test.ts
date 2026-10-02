// Oracles for the package boundary: the generated clients are what openapi-typescript makes of the
// checked artifacts now (a stale client fails); nothing shipped names a server secret or reads the
// environment; the App port imports only the consumer client and the Lab port only the Lab client.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const ROOT = join(import.meta.dirname, "..");
const REPO = join(ROOT, "..", "..");
const BIN = join(ROOT, "node_modules", ".bin", "openapi-typescript");
const PORTS = { app: join(REPO, "apps/app/lib/api"), lab: join(REPO, "apps/lab/lib/api") };

const shipped = (dir: string) => readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"))
  .map((f) => [join(dir, f), readFileSync(join(dir, f), "utf8")] as const);

test("the generated clients are current with the checked OpenAPI artifacts", () => {
  const out = mkdtempSync(join(tmpdir(), "api-client-"));
  for (const [artifact, client] of [["consumer", "consumer"], ["lab-control", "lab"]]) {
    const target = join(out, `${client}.ts`);
    execFileSync(BIN, [join(REPO, "apps/infrx-api/openapi", `${artifact}.json`), "-o", target], { stdio: "pipe" });
    assert.equal(readFileSync(join(ROOT, "src", `${client}.ts`), "utf8"), readFileSync(target, "utf8"), client);
  }
});

test("nothing shipped names a server secret or reads the environment", () => {
  for (const dir of [join(ROOT, "src"), PORTS.app, PORTS.lab]) {
    for (const [file, text] of shipped(dir)) {
      for (const banned of [/SUPABASE_SERVICE_ROLE_KEY/, /service_role/i, /\bsk-[A-Za-z0-9]/, /process\.env/, /DATABASE_URL/]) {
        assert.doesNotMatch(text, banned, file);
      }
    }
  }
});

test("the App port imports only the consumer client, the Lab port only the Lab client", () => {
  const imports = (dir: string) => shipped(dir).flatMap(([, text]) => [...text.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]));
  const app = imports(PORTS.app), lab = imports(PORTS.lab);
  assert.ok(app.some((s) => s.endsWith("api-client/src/consumer.ts")) && app.some((s) => s.endsWith("api-client/src/transport.ts")));
  assert.ok(lab.some((s) => s.endsWith("api-client/src/lab.ts")) && lab.some((s) => s.endsWith("api-client/src/transport.ts")));
  assert.ok(!app.some((s) => s.endsWith("/lab.ts") || s.includes("apps/lab")), app.join());
  assert.ok(!lab.some((s) => s.endsWith("/consumer.ts") || s.includes("apps/app")), lab.join());
});
