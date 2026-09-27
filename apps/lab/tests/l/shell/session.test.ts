// WR-L1-6: how a Lab session starts, ends and stays fresh: the sign-in/sign-out actions, the email-link
// callback and the proxy's refresh, run for real with Next's own redirect()/NextResponse, a fake
// request cookie store and a fake Supabase client (module hooks, as guard.test.ts does).
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";
import { ACCESS_COPY, SIGN_IN_COPY } from "../../../lib/auth/access.ts";
import { AUTH_COOKIE, WORKSPACE_COOKIE } from "../../../lib/auth/config.ts";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};

type Call = [string, ...unknown[]];
type World = { cookies: Record<string, string>; clients: unknown[][]; calls: Call[]; fail: boolean; refresh: boolean };
const world: World = ((globalThis as unknown as { labSession: World }).labSession = { cookies: {}, clients: [], calls: [], fail: false, refresh: false });

const FAKES: Record<string, string> = {
  "next/headers": `export async function cookies() {
    const c = globalThis.labSession.cookies;
    return { getAll: () => Object.entries(c).map(([name, value]) => ({ name, value })),
             get: (name) => (name in c ? { name, value: c[name] } : undefined),
             set: (name, value) => { c[name] = value; },
             delete: (name) => { delete c[name]; } };
  }`,
  // Every auth call is recorded; a success writes the session cookie through the client's setAll,
  // as @supabase/ssr does, so a test sees where the cookie went.
  "@supabase/ssr": `export function createServerClient(url, key, options) {
    const w = globalThis.labSession;
    w.clients.push([url, key, options]);
    const act = (name) => async (...args) => {
      w.calls.push([name, ...args]);
      if (w.fail) return { data: {}, error: { message: "Invalid login credentials", status: 400 } };
      if (name !== "getUser" || w.refresh)
        options.cookies.setAll([{ name: options.cookieOptions.name, value: name === "signOut" ? "" : "session", options: {} }]);
      return { data: { user: { id: "u1" } }, error: null };
    };
    return { auth: { signInWithPassword: act("signInWithPassword"), signOut: act("signOut"), getUser: act("getUser"),
                     exchangeCodeForSession: act("exchangeCodeForSession"), verifyOtp: act("verifyOtp") } };
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

const ENV = { NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co", NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon" };
function arrange(cookies: Record<string, string> = {}, env: object = ENV, fail = false) {
  Object.assign(world, { cookies, clients: [], calls: [], fail, refresh: false });
  delete process.env.NEXT_PUBLIC_SUPABASE_URL;
  delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
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
const labClientOnly = () => {
  assert.equal(world.clients.length, 1);
  const [url, key, options] = world.clients[0] as [string, string, { cookieOptions?: { name?: string; domain?: string } }];
  assert.deepEqual([url, key, options.cookieOptions?.name, options.cookieOptions?.domain], [ENV.NEXT_PUBLIC_SUPABASE_URL, ENV.NEXT_PUBLIC_SUPABASE_ANON_KEY, AUTH_COOKIE, undefined]);
};

test("L1-S01 sign-in sets the Lab session through the Lab's own client and lands on the home page", async () => {
  arrange();
  await assert.rejects(signIn(null, form({ email: "p@example.com", password: "pw" })), redirectsTo("/"));
  labClientOnly();
  assert.deepEqual(world.calls, [["signInWithPassword", { email: "p@example.com", password: "pw" }]]);
  assert.equal(world.cookies[AUTH_COOKIE], "session");
});

test("L1-S02 a failed or impossible sign-in is a fixed notice: never the auth server's words, never a session", async () => {
  arrange({}, ENV, true);
  assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com", password: "bad" }))), { error: "failed" });
  assert.deepEqual(world.cookies, {});
  arrange();
  assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com" }))), { error: "failed" });
  assert.deepEqual(world.calls, []);
  arrange({}, {});
  assert.deepEqual(await outcome(signIn(null, form({ email: "p@example.com", password: "pw" }))), { error: "unavailable" });
  assert.equal(world.clients.length, 0);
  assert.equal(SIGN_IN_COPY.unavailable, ACCESS_COPY.unavailable);
  assert.doesNotMatch(Object.values(SIGN_IN_COPY).join(" "), /invalid login credentials|sign up|credit/i);
});

test("L1-S03 sign-out ends the Lab session and forgets the workspace preference", async () => {
  arrange({ [AUTH_COOKIE]: "session", [WORKSPACE_COOKIE]: "11111111-1111-4111-8111-111111111111" });
  await assert.rejects(signOut(), redirectsTo("/"));
  labClientOnly();
  assert.deepEqual(world.calls, [["signOut"]]);
  assert.deepEqual(world.cookies, { [AUTH_COOKIE]: "" });
});

test("L1-S04 the email-link callback verifies the code or token on the Lab client and only ever returns home", async () => {
  arrange({}, { ...ENV, NEXT_PUBLIC_LAB_URL: "http://localhost:3100" });
  // The redirect is the configured origin's, never the request's Host (nor a `next` parameter).
  const pkce = await authCallback(new NextRequest("http://evil.example:3100/auth/callback?code=abc&next=https://evil.example/x"));
  labClientOnly();
  assert.deepEqual(world.calls, [["exchangeCodeForSession", "abc"]]);
  assert.equal(pkce.headers.get("location"), "http://localhost:3100/");
  assert.match(pkce.headers.get("set-cookie") ?? "", new RegExp(`^${AUTH_COOKIE}=session`));

  arrange();
  await authCallback(new NextRequest("http://localhost:3100/auth/callback?token_hash=t&type=magiclink"));
  assert.deepEqual(world.calls, [["verifyOtp", { token_hash: "t", type: "magiclink" }]]);
  // Consumer onboarding (signup, recovery) belongs to the App: those links verify nothing here.
  for (const type of ["signup", "recovery", "email_change"]) {
    arrange();
    const other = await authCallback(new NextRequest(`http://localhost:3100/auth/callback?token_hash=t&type=${type}`));
    assert.deepEqual(world.calls, []);
    assert.equal(other.headers.get("location"), "http://localhost:3100/");
  }
  arrange({}, {});
  const off = await authCallback(new NextRequest("http://localhost:3100/auth/callback?code=abc"));
  assert.deepEqual([world.clients.length, off.headers.get("location")], [0, "http://localhost:3100/"]);
});

test("L1-S05 the proxy refreshes the Lab session cookie on the response and never redirects", async () => {
  arrange();
  world.refresh = true;
  const request = new NextRequest("http://localhost:3100/models", { headers: { cookie: `${AUTH_COOKIE}=old` } });
  const response = await refreshSession(request);
  labClientOnly();
  assert.deepEqual(world.calls, [["getUser"]]);
  assert.equal(response.headers.get("location"), null);
  assert.equal(response.headers.get("x-middleware-next"), "1");
  assert.match(response.headers.get("set-cookie") ?? "", new RegExp(`^${AUTH_COOKIE}=session`));
  arrange({}, {});
  const off = await refreshSession(new NextRequest("http://localhost:3100/models"));
  assert.deepEqual([world.clients.length, off.headers.get("x-middleware-next")], [0, "1"]);
});
