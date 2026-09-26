// node --test "tests/**/*.test.ts"
//
// A2 wiring: the middleware lets an anonymous visitor reach signup and verify-email, still guards
// onboarding and set-a-new-password. The route table is read from source and the decision it makes is
// replayed here exactly (`path === p || path.startsWith(p + "/")`); the redirects themselves run on
// real requests in middleware.test.ts.
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

// A2-ROUTE-02 (a signed-in visitor is sent away from sign-in and signup) is now behavioural, on real
// requests: tests/a/middleware.test.ts A2-MW-01..03 (E3A F-1).

// app-union (C0 WR-1 follow-up): the console shell redirects unverified and onboarding individuals to
// A2's routes. Each target must be a shipped page outside the console layout (no redirect loop, no 404).
test("A2-ROUTE-03 the console shell's verify/onboarding redirects land on shipped pages outside the console", () => {
  const appRoot = join(resolve(dirname(fileURLToPath(import.meta.url)), "../.."), "app");
  const layout = readFileSync(join(appRoot, "(console)", "layout.tsx"), "utf8");
  const routes = /const ROUTES = \{ verifyEmail: "([^"]+)", onboarding: "([^"]+)" \};/.exec(layout);
  assert.ok(routes, "the console layout's ROUTES are not both set");
  assert.deepEqual([routes[1], routes[2]], ["/verify-email", "/welcome"]);
  for (const path of [routes[1], routes[2]]) {
    assert.ok(readFileSync(join(appRoot, "(auth)", path, "page.tsx"), "utf8").length > 0, `${path} has no page`);
  }
  assert.ok(isPublic("/verify-email"), "an unverified visitor must reach /verify-email");
});

// I2A WR-I2A-1: the release identity (commit, build time, deployment id, API origin) carries nothing
// per-person, so the post-deploy smoke can curl it without a session.
test("I2A-ROUTE-01 the release identity is public", () => {
  assert.ok(isPublic("/api/version"), "smoke S1 must reach /api/version without a session");
  assert.ok(!isPublic("/api/versions"), "only the exact identity route is public");
});
