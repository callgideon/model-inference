// WR-L1-6 + AP-09 09c: how a Lab session starts, ends and stays fresh - the sign-in/sign-out actions,
// the email-link callback and the proxy's refresh - through the auth facade (/auth/v1/* on
// LAB_API_URL), run for real with Next's own redirect()/NextResponse, a fake request cookie store
// (module hooks, as guard.test.ts does) and a fake API behind `fetch`.
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { ACCESS_COPY, SIGN_IN_COPY } from "../../../lib/auth/access.ts";
import { AUTH_COOKIE, REFRESH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};

type Sent = { method: string; url: string; auth: string | null; body: unknown };
type World = { cookies: Record<string, string>; options: Record<string, object>; sent: Sent[]; status: number; body: unknown };
const world: World = ((globalThis as unknown as { labSession: World }).labSession = { cookies: {}, options: {}, sent: [], status: 200, body: null });
globalThis.fetch = (async (url: string, init: RequestInit) => {
  world.sent.push({ method: String(init.method), url: String(url), auth: new Headers(init.headers).get("authorization"), body: init.body ? JSON.parse(String(init.body)) : null });
  if (world.status === 0) throw new Error("down");
  return new Response(world.status === 204 ? null : JSON.stringify(world.body), { status: world.status });
}) as typeof fetch;

const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    const w = globalThis.labSession, c = w.cookies;
    return { getAll: () => Object.entries(c).map(([name, value]) => ({ name, value })),
             get: (name) => (name in c ? { name, value: c[name] } : undefined),
             set: (name, value, options) => { c[name] = value; w.options[name] = options; },
             delete: (name) => { delete c[name]; } };
  }`,
};
registerHooks({
  resolve(specifier, context, next) {
    if (specifier in FAKES) return { url: `data:text/javascript,${encodeURIComponent(FAKES[specifier])}`, shortCircuit: true };
    return next(specifier === "next/navigation" ? "next/navigation.js" : specifier, context);
  },
});
const { signIn, signOut } = await import("../../../lib/auth/sign-in.ts");
const { authCallback, refreshSession } = await import("../../../lib/auth/routes.ts");
const { NextRequest } = await import("next/server.js");

const API = "https://lab-control.example";
const ENV = { LAB_API_URL: API };
/** A JWT-shaped access token expiring `inS` seconds from now (the proxy reads only `exp`). */
const jwt = (inS: number) => `h.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + inS })).toString("base64url")}.s`;
const TOKENS = { access_token: jwt(3600), refresh_token: "r-new", token_type: "bearer", expires_in: 3600, user_id: "u1" };
const ERROR = (code: string) => ({ error: { code, message: "That email and password do not match an account.", request_id: "q", retryable: false } });

function arrange(cookies: Record<string, string> = {}, env: object = ENV, status = 200, body: unknown = TOKENS) {
  Object.assign(world, { cookies, options: {}, sent: [], status, body });
  delete process.env.LAB_API_URL;
  delete process.env.NEXT_PUBLIC_LAB_URL;
  Object.assign(process.env, env);
}
const form = (fields: Record<string, string>) => {
  const data = new FormData();
  for (const [name, value] of Object.entries(fields)) data.set(name, value);
  return data;
};
const redirectsTo = (path: string) => (error: unknown) => {
  const digest = String((error as { digest?: string }).digest);
  return digest.startsWith("NEXT_REDIRECT;") && digest.split(";")[2] === path;
};
/** The action's answer, or where it redirected: a redirect is an answer to compare, not a crash. */
const outcome = (run: Promise<unknown>) => run.catch((error) => ({ redirected: String((error as { digest?: string }).digest).split(";")[2] }));
const setCookies = (r: Response) => r.headers.getSetCookie();

test("L1-S01 sign-in posts the credentials to the auth facade, keeps the session in the Lab's http-only cookies and lands home", async () => {
  arrange();
  await assert.rejects(signIn(null, form({ email: "p@example.com", password: "pw" })), redirectsTo("/"));
  assert.deepEqual(world.sent, [{ method: "POST", url: `${API}/auth/v1/sign-in`, auth: null, body: { email: "p@example.com", password: "pw" } }]);
  assert.deepEqual([world.cookies[AUTH_COOKIE], world.cookies[REFRESH_COOKIE]], [TOKENS.access_token, "r-new"]);
  for (const name of [AUTH_COOKIE, REFRESH_COOKIE]) assert.equal((world.options[name] as { httpOnly?: boolean }).httpOnly, true, name);
});

test("L1-S02 a failed or impossible sign-in is a fixed notice: never the auth server's words, never a session", async () => {
  for (const status of [401, 403, 422]) {
    arrange({}, ENV, status, ERROR("invalid_credentials"));
    assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com", password: "bad" }))), { error: "failed" }, String(status));
    assert.deepEqual(world.cookies, {});
  }
  for (const [status, body] of [[429, ERROR("rate_limited")], [503, ERROR("unavailable")], [0, null], [200, { access_token: 1, refresh_token: "r" }], [200, { access_token: "a" }]] as const) {
    arrange({}, ENV, status, body);
    assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com", password: "pw" }))), { error: "unavailable" }, String(status));
    assert.deepEqual(world.cookies, {});
  }
  arrange();
  assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com" }))), { error: "failed" });
  assert.deepEqual(world.sent, []);
  arrange({}, {});
  assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com", password: "pw" }))), { error: "unavailable" });
  assert.deepEqual(world.sent, []);
  assert.equal(SIGN_IN_COPY.unavailable, ACCESS_COPY.unavailable);
  assert.doesNotMatch(Object.values(SIGN_IN_COPY).join(" "), /invalid login credentials|do not match an account|sign up|credit/i);
});

test("L1-S03 sign-out ends the session at the auth facade, drops both session cookies and forgets the workspace preference", async () => {
  arrange({ [AUTH_COOKIE]: "access", [REFRESH_COOKIE]: "refresh", [WORKSPACE_COOKIE]: "11111111-1111-4111-8111-111111111111" }, ENV, 204, null);
  await assert.rejects(signOut(), redirectsTo("/"));
  assert.deepEqual(world.sent, [{ method: "POST", url: `${API}/auth/v1/sign-out`, auth: "Bearer access", body: null }]);
  assert.deepEqual(world.cookies, {});
  // The facade down or no session: the Lab still forgets it.
  arrange({ [AUTH_COOKIE]: "access", [REFRESH_COOKIE]: "refresh" }, ENV, 0, null);
  await assert.rejects(signOut(), redirectsTo("/"));
  assert.deepEqual(world.cookies, {});
  arrange({ [WORKSPACE_COOKIE]: "x" });
  await assert.rejects(signOut(), redirectsTo("/"));
  assert.deepEqual([world.sent, world.cookies], [[], {}]);
});

test("L1-S04 the email-link callback verifies the code or token at the auth facade and only ever returns home", async () => {
  arrange({}, { ...ENV, NEXT_PUBLIC_LAB_URL: "http://localhost:3100" }, 200, { session: TOKENS, redirect: "https://evil.example/x" });
  // The redirect is the configured origin's, never the request's Host, a `next` parameter or the facade's redirect.
  const pkce = await authCallback(new NextRequest("http://evil.example:3100/auth/callback?code=abc&next=https://evil.example/x"));
  assert.deepEqual(world.sent, [{ method: "GET", url: `${API}/auth/v1/callback?code=abc`, auth: null, body: null }]);
  assert.equal(pkce.headers.get("location"), "http://localhost:3100/");
  const set = setCookies(pkce);
  assert.ok(set.some((c) => c.startsWith(`${AUTH_COOKIE}=${TOKENS.access_token};`) && /HttpOnly/i.test(c)), set.join("\n"));
  assert.ok(set.some((c) => c.startsWith(`${REFRESH_COOKIE}=r-new;`)), set.join("\n"));

  arrange({}, ENV, 200, { session: TOKENS, redirect: "/" });
  await authCallback(new NextRequest("http://localhost:3100/auth/callback?token_hash=t&type=magiclink"));
  assert.deepEqual(world.sent.map((s) => s.url), [`${API}/auth/v1/callback?token_hash=t&type=magiclink`]);
  // Consumer onboarding (signup, recovery) belongs to the App: those links verify nothing here.
  for (const type of ["signup", "recovery", "email_change"]) {
    arrange();
    const other = await authCallback(new NextRequest(`http://localhost:3100/auth/callback?token_hash=t&type=${type}`));
    assert.deepEqual(world.sent, []);
    assert.equal(other.headers.get("location"), "http://localhost:3100/");
  }
  arrange({}, ENV, 410, ERROR("link_expired"));
  const expired = await authCallback(new NextRequest("http://localhost:3100/auth/callback?code=abc"));
  assert.deepEqual([setCookies(expired), expired.headers.get("location")], [[], "http://localhost:3100/"]);
  arrange({}, {});
  const off = await authCallback(new NextRequest("http://localhost:3100/auth/callback?code=abc"));
  assert.deepEqual([world.sent.length, off.headers.get("location")], [0, "http://localhost:3100/"]);
});

test("L1-S05 the proxy refreshes a session near expiry at the auth facade, on the response and the request; it never redirects", async () => {
  const proxied = (cookies: Record<string, string>) =>
    refreshSession(new NextRequest("http://localhost:3100/models", { headers: { cookie: Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ") } }));
  // Fresh: nothing is sent, nothing is set.
  arrange();
  let response = await proxied({ [AUTH_COOKIE]: jwt(3600), [REFRESH_COOKIE]: "r-old" });
  assert.deepEqual([world.sent, setCookies(response), response.headers.get("x-middleware-next")], [[], [], "1"]);
  // Within a minute of expiry, expired, unreadable or missing: one refresh with the refresh token.
  for (const access of [jwt(30), jwt(-5), "not-a-jwt", `h.${Buffer.from("{}").toString("base64url")}.s`, undefined]) {
    arrange();
    response = await proxied({ ...(access === undefined ? {} : { [AUTH_COOKIE]: access }), [REFRESH_COOKIE]: "r-old" });
    assert.deepEqual(world.sent, [{ method: "POST", url: `${API}/auth/v1/refresh`, auth: null, body: { refresh_token: "r-old" } }], String(access));
    assert.equal(response.headers.get("location"), null);
    assert.equal(response.headers.get("x-middleware-next"), "1");
    assert.ok(setCookies(response).some((c) => c.startsWith(`${AUTH_COOKIE}=${TOKENS.access_token};`)));
    // This request's pages see the new token too.
    assert.match(response.headers.get("x-middleware-request-cookie") ?? "", new RegExp(`${AUTH_COOKIE}=${TOKENS.access_token.replace(/\./g, "\\.")}`));
  }
  // A refused refresh token ends the session; an unreachable facade leaves it for the next request.
  arrange({}, ENV, 401, ERROR("unauthenticated"));
  response = await proxied({ [AUTH_COOKIE]: jwt(-5), [REFRESH_COOKIE]: "r-old" });
  assert.deepEqual(setCookies(response).map((c) => c.split("=")[0]).sort(), [AUTH_COOKIE, REFRESH_COOKIE].sort());
  assert.ok(setCookies(response).every((c) => /Max-Age=0|Expires=Thu, 01 Jan 1970/i.test(c)));
  for (const status of [0, 429, 503]) {
    arrange({}, ENV, status, ERROR("unavailable"));
    response = await proxied({ [AUTH_COOKIE]: jwt(-5), [REFRESH_COOKIE]: "r-old" });
    assert.deepEqual([setCookies(response), response.headers.get("location")], [[], null], String(status));
  }
  // Signed out or misconfigured: nothing is sent.
  arrange();
  await proxied({ [AUTH_COOKIE]: jwt(-5) });
  assert.equal(world.sent.length, 0);
  arrange({}, {});
  const off = await proxied({ [REFRESH_COOKIE]: "r-old" });
  assert.deepEqual([world.sent.length, off.headers.get("x-middleware-next")], [0, "1"]);
});
