// node --test "tests/**/*.test.ts"
//
// E3A F-1: the middleware's decision, on real NextRequests. A server action on /login or /signup
// (the sign-in and its first-login grant claim) must reach its handler; a signed-in navigation to
// /login or /signup is still sent away; the guard for signed-out visitors is unchanged.
// AP-09 09b/09e: the session is the App's own cookie from the auth facade; the middleware refreshes it
// shortly before expiry through `POST /auth/v1/refresh`, ends it only on the facade's 401, and keeps
// it through an outage.
import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server.js";
import { decodeSession, encodeSession, SESSION_COOKIE } from "../../lib/api/cookie.ts";
import { facadeRefresh, redirectFor, updateSession, type Renewal } from "../../lib/api/middleware.ts";

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

const now = () => Math.floor(Date.now() / 1000);
const issued = (expiresIn: number, access = "eyJhbGciOiJIUzI1NiJ9.e30.sig") => ({
  access_token: access, refresh_token: "r-1", token_type: "bearer", expires_in: expiresIn, expires_at: now() + expiresIn, user_id: "u",
});
const withCookie = (path: string, value: string) => new NextRequest(ORIGIN + path, { headers: { cookie: `${SESSION_COOKIE}=${value}` } });

test("AP09-MW-01 a fresh session is served as signed in and never refreshed", async () => {
  let asked = 0;
  const response = await updateSession(withCookie("/usage", encodeSession(issued(3600), now())), async () => {
    asked += 1;
    return "unavailable";
  });
  assert.equal(asked, 0);
  assert.equal(response.headers.get("location"), null, "a signed-in visitor reaches the console");
});

test("AP09-MW-02 an expiring session is refreshed through the facade and the new one is set", async () => {
  const seen: string[] = [];
  const renewed = issued(3600, "eyJhbGciOiJIUzI1NiJ9.e30.new");
  const response = await updateSession(withCookie("/usage", encodeSession(issued(30), now())), async (token) => {
    seen.push(token);
    return renewed;
  });
  assert.deepEqual(seen, ["r-1"], "the stored refresh token, once");
  assert.equal(response.headers.get("location"), null);
  assert.equal(decodeSession(response.cookies.get(SESSION_COOKIE)?.value)?.access, renewed.access_token);
});

test("AP09-MW-03 a refresh the facade refuses ends the session: cookie cleared, guarded route goes to /login", async () => {
  const response = await updateSession(withCookie("/usage", encodeSession(issued(10), now())), async (): Promise<Renewal> => "ended");
  assert.match(response.headers.get("location") ?? "", /\/login\?next=%2Fusage$/);
  assert.equal(response.cookies.get(SESSION_COOKIE)?.value, "", "the dead session's cookie is dropped");
});

test("AP09-MW-04 an outage during refresh signs nobody out and keeps the cookie", async () => {
  const response = await updateSession(withCookie("/usage", encodeSession(issued(10), now())), async (): Promise<Renewal> => "unavailable");
  assert.equal(response.headers.get("location"), null);
  assert.equal(response.cookies.get(SESSION_COOKIE), undefined, "nothing is set or cleared");
});

test("AP09-MW-05 facadeRefresh: 200 is the new session, only 401 is ended, anything else is unavailable", async () => {
  process.env.INFRX_API_BASE_URL = "http://127.0.0.1:9";
  const body = issued(3600);
  const answer = (status: number, json: unknown) => (async () => new Response(JSON.stringify(json), { status })) as unknown as typeof fetch;
  assert.deepEqual(await facadeRefresh("r-1", answer(200, body)), body);
  const envelope = (code: string) => ({ error: { code, message: "m", request_id: "r", retryable: false } });
  assert.equal(await facadeRefresh("r-1", answer(401, envelope("unauthenticated"))), "ended");
  assert.equal(await facadeRefresh("r-1", answer(429, envelope("rate_limited"))), "unavailable");
  assert.equal(await facadeRefresh("r-1", answer(503, envelope("unavailable"))), "unavailable");
  assert.equal(await facadeRefresh("r-1", (async () => { throw new Error("down"); }) as unknown as typeof fetch), "unavailable");
});

test("AP09-MW-06 a cookie that is not exactly a session (no refresh token, no expiry, not ours) is signed out", async () => {
  const bad = [
    btoa(JSON.stringify({ access: "eyJ.a.b", refresh: "", expiresAt: now() + 3600 })),
    btoa(JSON.stringify({ access: "eyJ.a.b", refresh: "r", expiresAt: "soon" })),
    "not-base64-json",
  ];
  for (const value of bad) {
    assert.equal(decodeSession(value), null, value);
    const response = await updateSession(withCookie("/usage", value), async () => assert.fail("no refresh for a non-session"));
    assert.match(response.headers.get("location") ?? "", /\/login\?next=%2Fusage$/, value);
  }
});
