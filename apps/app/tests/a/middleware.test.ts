// node --test "tests/**/*.test.ts"
//
// E3A F-1: the middleware's decision, on real NextRequests. The login form signs in in the browser
// (the session cookie is set) and then calls the server action `claimOnboarding`, which POSTs to
// /login. Redirecting that POST to /models dropped the action, so the first-login grant claim never
// ran. A signed-in navigation to /login or /signup is still sent away; the guard for signed-out
// visitors is unchanged.
import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server.js";
import { redirectFor } from "../../lib/supabase/middleware.ts";

const ORIGIN = "http://localhost:3000";
const request = (method: string, path: string, action = false) =>
  new NextRequest(ORIGIN + path, { method, headers: action ? { "next-action": "0123abcd" } : {} });
const target = (to: ReturnType<typeof redirectFor>) => (to === null ? null : to.pathname + to.search);

test("A2-MW-01 a signed-in navigation to sign-in or signup is sent to /models", () => {
  for (const method of ["GET", "HEAD"]) {
    for (const path of ["/login", "/signup", "/login?next=%2Fusage"]) {
      assert.equal(target(redirectFor(request(method, path), true)), "/models", `${method} ${path}`);
    }
  }
});

test("A2-MW-02 a signed-in server action POST to /login or /signup passes through (the sign-in claim runs)", () => {
  for (const path of ["/login", "/login?next=%2Fusage", "/signup"]) {
    assert.equal(redirectFor(request("POST", path, true), true), null, `POST ${path} must reach its server action`);
  }
});

test("A2-MW-03 a signed-out visitor to a guarded route goes to /login with next; public routes are served", () => {
  assert.equal(target(redirectFor(request("GET", "/usage?range=7d"), false)), "/login?next=%2Fusage%3Frange%3D7d");
  assert.equal(target(redirectFor(request("POST", "/api-keys", true), false)), "/login?next=%2Fapi-keys");
  for (const path of ["/login", "/signup", "/verify-email", "/auth/callback"]) {
    assert.equal(redirectFor(request("GET", path), false), null, path);
  }
  assert.equal(redirectFor(request("GET", "/usage"), true), null, "a signed-in visitor reaches the console");
});
