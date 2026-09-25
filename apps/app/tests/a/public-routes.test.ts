// node --test "tests/**/*.test.ts"
//
// A2 wiring: the middleware lets an anonymous visitor reach signup and verify-email, still guards
// onboarding and set-a-new-password, and sends a signed-in visitor away from signup. The middleware
// imports next/server, which `node --test` cannot load, so its route table is read from source and
// the decision it makes is replayed here exactly (`path === p || path.startsWith(p + "/")`).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const source = readFileSync(
  join(resolve(dirname(fileURLToPath(import.meta.url)), "../.."), "lib", "supabase", "middleware.ts"),
  "utf8",
);
const table = /const PUBLIC = \[([^\]]*)\]/.exec(source);
const PUBLIC = table ? [...table[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
const isPublic = (path: string) => PUBLIC.some((p) => path === p || path.startsWith(p + "/"));

test("A2-ROUTE-01 signup and verify-email are public; onboarding and password change are not", () => {
  assert.ok(PUBLIC.length > 0, "the middleware's PUBLIC table was not found");
  for (const path of ["/signup", "/verify-email", "/login", "/forgot-password", "/auth/callback"]) {
    assert.ok(isPublic(path), `${path} must be reachable without a session`);
  }
  for (const path of ["/welcome", "/update-password", "/api-keys", "/usage", "/signupx", "/admin"]) {
    assert.ok(!isPublic(path), `${path} must require a session`);
  }
});

test("A2-ROUTE-02 a signed-in visitor is sent away from sign-in and signup", () => {
  assert.match(source, /if \(user && \(path === "\/login" \|\| path === "\/signup"\)\)/);
});
