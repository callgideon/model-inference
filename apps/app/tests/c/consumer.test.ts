// node --test "tests/**/*.test.ts"
//
// C0 over infrx-api (AP-09 09a): the signed-in individual's context (`GET /console/v1/me`) and read
// port (`/console/v1/*`), through the generated client over the client fake and recorded answers.
// Each case names the broken behaviour it catches: an outage read as signed out or onboarding, a
// figure relabelled or guessed, a page that drops the API's cursor, a refusal's text leaking.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { answer, defaultWorld, envelope, fakeConsoleApi, FAKE_KEY, FAKE_ORG, FAKE_USER, recordingApi } from "../../lib/api/fake.ts";
import { onceGets } from "../../lib/api/result.ts";
import { apiConsumerReads, consoleShell, consumerContextFrom, consumerSessionFrom, providerRoute } from "../../lib/services/console.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const EMAIL = "me@example.com";
const WALLET = "a1000000-0000-4000-8000-00000000000a";
const SHIPPED = { verifyEmail: "/verify-email", onboarding: "/onboarding" };
const NOT_SHIPPED = { verifyEmail: null, onboarding: null };
const ACCOUNT = { userId: FAKE_USER, email: EMAIL, walletId: WALLET, orgId: FAKE_ORG, suspended: false };

/** The default world with `me` and `credits` changed. */
function worldWith(change: (w: ReturnType<typeof defaultWorld>) => void) {
  const world = defaultWorld();
  change(world);
  return fakeConsoleApi(world);
}

/** The fake, except `path` answers `reply` (a status + envelope, or a throw). */
function failing(path: string, reply: () => Response) {
  const fake = fakeConsoleApi();
  return recordingApi(async (sent) => {
    if (sent.path.split("?")[0] === path) return reply();
    const real = await fake.call(sent.method.toLowerCase() as "get", sent.path.split("?")[0] as "/console/v1/me");
    return real.ok ? answer(200, real.data) : answer(real.error.status ?? 503, envelope("dependency_unavailable"));
  });
}

// ------------------------------------------------------------------------------ the context ---

test("the context is GET /console/v1/me: ready carries the session's own org, wallet and suspension", async () => {
  assert.deepEqual(await consumerContextFrom(fakeConsoleApi(), EMAIL), { state: "ready", account: ACCOUNT });
});

test("signed out is only the API's 401; an outage or a malformed answer is unavailable, never signed out", async () => {
  const gone = recordingApi(() => answer(401, envelope("invalid_api_key")));
  assert.deepEqual(await consumerContextFrom(gone.api, EMAIL), { state: "signed_out" });
  for (const reply of [() => answer(503, envelope("dependency_unavailable")), () => new Response("<html>", { status: 502 }), () => { throw new Error("down"); }]) {
    assert.deepEqual(await consumerContextFrom(recordingApi(reply).api, EMAIL), { state: "unavailable" });
  }
});

test("unverified and onboarding are the server's states and read nothing more", async () => {
  for (const state of ["unverified", "onboarding"] as const) {
    const api = worldWith((w) => (w.me!.state = state));
    assert.deepEqual(await consumerContextFrom(api, EMAIL), { state, userId: FAKE_USER, email: EMAIL });
    const session = await consumerSessionFrom(api, EMAIL);
    assert.ok(session.reads === null, `${state}: no reads`);
  }
});

test("a ready account whose wallet cannot be read is unavailable, never onboarding (which would offer a second grant)", async () => {
  const { api } = failing("/console/v1/credits", () => answer(503, envelope("dependency_unavailable")));
  assert.deepEqual(await consumerContextFrom(api, EMAIL), { state: "unavailable" });
  const noWallet = worldWith((w) => (w.credits.wallet_id = null));
  assert.deepEqual(await consumerContextFrom(noWallet, EMAIL), { state: "unavailable" }, "ready without a wallet is not an account");
});

test("an actor without a usable user or org id is unavailable, not a guess", async () => {
  assert.deepEqual(await consumerContextFrom(worldWith((w) => (w.me!.actor.user_id = null)), EMAIL), { state: "unavailable" });
  assert.deepEqual(await consumerContextFrom(worldWith((w) => (w.me!.actor.org_id = "not-an-org")), EMAIL), { state: "unavailable" });
});

test("a suspended account is still the account, marked suspended", async () => {
  const suspended = await consumerContextFrom(worldWith((w) => (w.me!.suspended = true)), EMAIL);
  assert.deepEqual(suspended, { state: "ready", account: { ...ACCOUNT, suspended: true } });
});

// -------------------------------------------------------------------------------- the reads ---

const reads = (api = fakeConsoleApi()) => apiConsumerReads(api, { orgId: FAKE_ORG, walletId: WALLET });

test("reads.balance: the API's exact CREDIT figures, for this wallet only", async () => {
  assert.deepEqual(await reads().balance(), {
    ok: true,
    value: { schema_version: 2, wallet_id: WALLET, kind: "consumer", unit: "CREDIT", ledger_total: "9992.67777779", reserved_total: "10.00000000", available: "9982.67777779" },
  });
  const other = await apiConsumerReads(fakeConsoleApi(), { orgId: FAKE_ORG, walletId: "a1000000-0000-4000-8000-0000000000ff" }).balance();
  assert.equal(other.ok ? "ok" : other.error.code, "internal_error", "another wallet's balance is not a balance");
  const usd = await reads(worldWith((w) => (w.credits.available = { amount: "1.00000000", unit: "USD" }))).balance();
  assert.equal(usd.ok ? "ok" : usd.error.code, "internal_error", "a USD figure is never a CREDIT one");
});

test("legacy USD is its own statement: none is null, history is exact USD, never CREDIT", async () => {
  assert.deepEqual(await reads().legacyUsd(), {
    ok: true,
    value: { schema_version: 2, org_id: FAKE_ORG, balance: "4.99980340", entry_count: 2, as_of: "2026-09-20T12:00:00.000000Z", rollout_hold: true },
  });
  assert.deepEqual(await reads(worldWith((w) => (w.legacy.entry_count = 0))).legacyUsd(), { ok: true, value: null });
  const relabelled = await reads(worldWith((w) => (w.legacy.balance = { amount: "1.00000000", unit: "CREDIT" }))).legacyUsd();
  assert.equal(relabelled.ok ? "ok" : relabelled.error.code, "internal_error");
});

test("the CREDIT ledger pages on the API's own cursor, newest first, exact CREDIT", async () => {
  const first = await reads().ledger({ limit: 2 });
  assert.ok(first.ok, "the first ledger page");
  assert.deepEqual(first.value.items.map((e) => e.amount), ["-0.98765432", "-1.23456789"], "ties by entry id, descending");
  assert.equal(first.value.next_cursor, "2", "the API's cursor, passed back as given");
  const second = await reads().ledger({ limit: 2, cursor: first.value.next_cursor! });
  assert.ok(second.ok, "the second ledger page");
  assert.deepEqual(second.value.items.map((e) => e.kind), ["inference_debit", "operator_adjustment"]);
  const { api, sent } = recordingApi(() => answer(200, { data: [], next_cursor: null }));
  await reads(api).ledger({ limit: 5, cursor: "opaque" });
  assert.deepEqual(sent.map((s) => s.path), ["/console/v1/credit-ledger?limit=5&cursor=opaque"], "no tenant argument, the cursor untouched");
});

test("a limit outside 1..100 is refused before any call", async () => {
  const { api, sent } = recordingApi(() => answer(200, { data: [] }));
  for (const limit of [0, 101, 1.5]) {
    const page = await reads(api).ledger({ limit });
    assert.equal(page.ok ? "ok" : page.error.code, "invalid_request", `limit ${limit}`);
  }
  assert.deepEqual(sent, []);
});

test("a ledger entry that is not CREDIT, or of an unknown kind, fails the page instead of rendering", async () => {
  for (const change of [(w: ReturnType<typeof defaultWorld>) => (w.ledger[0].amount = { amount: "1.00000000", unit: "USD" }), (w: ReturnType<typeof defaultWorld>) => (w.ledger[0].kind = "free_money")]) {
    const page = await reads(worldWith(change)).ledger({ limit: 10 });
    assert.equal(page.ok ? "ok" : page.error.code, "internal_error");
  }
});

test("a refusal keeps its contract code with fixed text; a stale cursor is invalid_cursor; an outage is dependency_unavailable", async () => {
  const stale = await reads(recordingApi(() => answer(400, envelope("invalid_cursor"))).api).ledger({ limit: 5, cursor: "x" });
  assert.deepEqual(stale.ok ? null : stale.error.code, "invalid_cursor");
  assert.ok(!stale.ok && !stale.error.message.includes("SECRET"), "the API's text never reaches the page");
  const down = await reads(recordingApi(() => { throw new Error("down"); }).api).keys();
  assert.equal(down.ok ? "ok" : down.error.code, "dependency_unavailable");
  const odd = await reads(recordingApi(() => answer(500, envelope("something_new"))).api).keys();
  assert.equal(odd.ok ? "ok" : odd.error.code, "dependency_unavailable", "an unknown code is 'not now'");
});

test("requests: exact charge in the request's own unit; unsettled is null, never a zero; the result state is the API's", async () => {
  const page = await reads().requests({ limit: 10 });
  assert.ok(page.ok, "the requests page");
  const byId = Object.fromEntries(page.value.items.map((r) => [r.request_id.slice(-1), r]));
  assert.deepEqual([byId["1"].unit, byId["1"].charged, byId["1"].result], ["CREDIT", "1.23456789", "available"]);
  assert.deepEqual([byId["3"].charged, byId["3"].result], [null, "pending"], "running: nothing charged yet");
  assert.deepEqual([byId["8"].unit, byId["8"].charged, byId["8"].hold], ["USD", "0.00019660", "0.00500000"], "a legacy request stays USD");
  assert.deepEqual(byId["4"].result, "held_unknown");
  assert.deepEqual(byId["3"].usage, null);
  assert.deepEqual(byId["1"].usage, { prompt_tokens: 1200, completion_tokens: 340 });
});

test("requests: a charge whose unit contradicts the regime, or half a usage report, fails the page", async () => {
  const relabelled = await reads(worldWith((w) => (w.requests[0].charged = { amount: "1.00000000", unit: "USD" }))).requests({});
  assert.equal(relabelled.ok ? "ok" : relabelled.error.code, "internal_error");
  const half = await reads(worldWith((w) => (w.requests[0].completion_tokens = null))).requests({});
  assert.equal(half.ok ? "ok" : half.error.code, "internal_error");
});

test("request detail and result: a malformed id is not_found without a call; the owned body through the API", async () => {
  const { api, sent } = recordingApi(() => answer(404, envelope("not_found")));
  const bad = await reads(api).request("../../keys");
  assert.equal(bad.ok ? "ok" : bad.error.code, "not_found");
  assert.equal((await reads(api).result("nope")).ok, false);
  assert.deepEqual(sent, [], "a malformed id never reaches the API");
  const one = await reads().request("b1000000-0000-4000-8000-000000000001");
  assert.ok(one.ok && one.value.request_id === "b1000000-0000-4000-8000-000000000001");
  const body = await reads().result("b1000000-0000-4000-8000-000000000001");
  assert.ok(body.ok && body.value.startsWith("Demo result"));
  const expired = await reads(recordingApi(() => answer(410, envelope("result_expired"))).api).result("b1000000-0000-4000-8000-000000000001");
  assert.equal(expired.ok ? "ok" : expired.error.code, "result_expired");
});

test("keys: this account's key metadata from GET /console/v1/keys, capture off (null), never a hash", async () => {
  const keys = await reads().keys();
  assert.deepEqual(keys, {
    ok: true,
    value: [{ id: FAKE_KEY, name: "preview", prefix: "sk-infrx-previ", created_at: "2026-09-20T08:00:00.000000Z", last_used_at: null, revoked_at: null, trace_mode: null }],
  });
});

// ------------------------------------------------------------------------------- the shell ---

test("consumerSession: signed out, unverified and onboarding carry no reads; a ready account does", async () => {
  assert.deepEqual(await consumerSessionFrom(recordingApi(() => answer(401, envelope("invalid_api_key"))).api, EMAIL), { context: { state: "signed_out" }, reads: null });
  const ready = await consumerSessionFrom(fakeConsoleApi(), EMAIL);
  assert.deepEqual(ready.context, { state: "ready", account: ACCOUNT });
  assert.ok(ready.reads !== null, "a ready account carries its reads");
  const keys = await ready.reads!.keys();
  assert.ok(keys.ok, "the reads run on the same client");
});

test("the console shell: an operator without a consumer wallet reaches /admin, not /onboarding", async () => {
  const operator = await consumerSessionFrom(worldWith((w) => (w.me!.state = "onboarding")), EMAIL);
  assert.deepEqual(consoleShell(operator, true, SHIPPED), { kind: "render", email: EMAIL, reads: null });
  assert.deepEqual(consoleShell(operator, false, SHIPPED), { kind: "redirect", to: "/onboarding" });
  assert.deepEqual(consoleShell(operator, false, NOT_SHIPPED), { kind: "panel", state: "onboarding" }, "never a redirect to a missing route");
});

test("the console shell: a refused session goes to /auth/expired (which clears the cookie), verification, readiness and outages", async () => {
  assert.deepEqual(consoleShell({ context: { state: "signed_out" }, reads: null }, false, SHIPPED), { kind: "redirect", to: "/auth/expired" });
  const unverified = { context: { state: "unverified" as const, userId: FAKE_USER, email: EMAIL }, reads: null };
  assert.deepEqual(consoleShell(unverified, true, SHIPPED), { kind: "redirect", to: "/verify-email" }, "an operator verifies too");
  assert.deepEqual(consoleShell(unverified, false, NOT_SHIPPED), { kind: "panel", state: "unverified" });
  assert.deepEqual(consoleShell({ context: { state: "unavailable" }, reads: null }, true, SHIPPED), { kind: "panel", state: "unavailable" });
  const ready = await consumerSessionFrom(fakeConsoleApi(), EMAIL);
  const shell = consoleShell(ready, false, SHIPPED);
  assert.ok(shell.kind === "render" && shell.email === EMAIL && shell.reads === ready.reads, "the page gets the account's reads");
  assert.deepEqual(consoleShell({ context: ready.context, reads: null }, false, SHIPPED), { kind: "panel", state: "unavailable" }, "ready without reads");
});

test("U1R-AUTH-01 one answer per GET per request: the layout, the sidebar and the page share it (onceGets)", () => {
  const server = readFileSync(join(appRoot, "lib", "api", "server.ts"), "utf8");
  assert.match(server, /fetch: onceGets\(fetch\)/, "the request's client memoises its GETs");
  assert.match(server, /export const apiSource = cache\(/, "one client per request");
  assert.match(readFileSync(join(appRoot, "lib", "services", "server.ts"), "utf8"), /export const consumerSession = cache\(/);
});

test("U1R-AUTH-02 onceGets: one network call per GET url, every caller a fresh body; writes are never memoised", async () => {
  let calls = 0;
  const once = onceGets((async () => {
    calls += 1;
    return new Response('{"n":1}', { status: 200 });
  }) as unknown as typeof fetch);
  const [a, b] = await Promise.all([once("http://x/console/v1/me"), once("http://x/console/v1/me")]);
  assert.deepEqual([await a.json(), await b.json()], [{ n: 1 }, { n: 1 }]);
  assert.equal(calls, 1);
  await once("http://x/console/v1/me", { method: "POST" });
  await once("http://x/console/v1/me", { method: "POST" });
  assert.equal(calls, 3, "a POST always reaches the API");
});

test("C-PROV-00 providerRoute is exactly an operator check", () => {
  assert.throws(() => providerRoute({ isOperator: false }, () => { throw new Error("404"); }), /404/);
  providerRoute({ isOperator: true }, () => { throw new Error("404"); });
});
