// node --test "tests/**/*.test.ts"
//
// C3A over infrx-api (AP-09 09b): the trusted consumer actions (CONSOLE-FLOWS, CREDIT-SPEND's "no
// optimistic success"). Each case names the defect it catches:
// - an input with a smuggled org/role/audience/amount/actor is refused before any call;
// - a key is minted by the API (one POST /console/v1/keys with the dialog's Idempotency-Key): the
//   App holds no generator, no hash and no replay memory; the plaintext only on the first answer, a
//   replay (`secret_returned: false`) is "created, revoke and create another", never a secret;
// - revocation is DELETE /console/v1/keys/{id}; another tenant's key is the API's 404;
// - the server actions refuse a cross-site request before anything, and revalidate only after an
//   acknowledged change; the API's own text never reaches a caller; an unknown outcome says so;
// - operator actions need operator authority, a reason and an idempotency key; the actor is the session.

import assert from "node:assert/strict";
import test from "node:test";
import { answer, envelope, recordingApi, type Sent } from "../../lib/api/fake.ts";
import type { Result } from "../../lib/contracts/types.ts";
import {
  consoleActions,
  createKey,
  CREATE_UNKNOWN,
  operatorCommand,
  revokeKey,
  runOperatorCommand,
  sameOrigin,
  type ActionDeps,
  type OperatorPort,
} from "../../lib/services/actions.ts";

const ME = "c1000000-0000-4000-8000-000000000001";
const OTHER = "c1000000-0000-4000-8000-000000000002";
const MY_ORG = "0e000000-0000-4000-8000-000000000001";
const KEY = "c7000000-0000-4000-8000-000000000001";
const AT = "2026-09-25T12:00:00Z";
const AT6 = "2026-09-25T12:00:00.000000Z";
const API_KEY = { key_id: KEY, name: "ci", prefix: "sk-infrx-abcd1234", created_at: AT, revoked_at: null };
const CREATED = { key: API_KEY, secret: "sk-infrx-" + "s".repeat(40), secret_returned: true, replayed: false };

function valueOf<T>(result: Result<T>, what: string): T {
  if (!result.ok) assert.fail(`${what}: expected success, got ${result.error.code} (${result.error.message})`);
  return result.value;
}

function codeOf(result: Result<unknown>): string {
  return result.ok ? "ok" : result.error.code;
}

const calls = (sent: Sent[]) => sent.map((s) => `${s.method} ${s.path}`);

// ------------------------------------------------------------------------------------ key create

test("key create: one POST /console/v1/keys with the name and the dialog's idempotency key; the secret once", async () => {
  const { api, sent } = recordingApi(() => answer(201, CREATED));
  const created = valueOf(await createKey(api, { name: "  ci  ", idempotency_key: "dialog-1" }), "create");
  assert.deepEqual(sent.map((s) => [s.method, s.path, s.body, s.idempotencyKey]), [["POST", "/console/v1/keys", { name: "ci" }, "dialog-1"]]);
  assert.deepEqual(created, { id: KEY, name: "ci", prefix: "sk-infrx-abcd1234", created_at: AT6, last_used_at: null, revoked_at: null, trace_mode: null, secret: CREATED.secret, replayed: false });
  const anonymous = recordingApi(() => answer(201, CREATED));
  await createKey(anonymous.api, { name: "ci" });
  assert.match(anonymous.sent[0].idempotencyKey ?? "", /^[0-9a-f-]{36}$/, "no dialog key: a fresh one, so the API still dedupes this call");
});

test("key create: a smuggled org, role, audience, amount or unknown field is refused before any call", async () => {
  const { api, sent } = recordingApi(() => answer(201, CREATED));
  for (const field of ["org_id", "role", "audience", "amount", "created_by", "key_hash", "secret"]) {
    assert.equal(codeOf(await createKey(api, { name: "k", [field]: "x" })), "invalid_request", field);
  }
  assert.equal(codeOf(await createKey(api, null)), "invalid_request");
  assert.equal(codeOf(await createKey(api, ["k"])), "invalid_request");
  assert.deepEqual(sent, []);
});

test("key create: name, idempotency key and capture validation (capture is not offered to consumers)", async () => {
  const { api, sent } = recordingApi(() => answer(201, CREATED));
  for (const name of ["", "   ", "x".repeat(201), 7]) assert.equal(codeOf(await createKey(api, { name })), "invalid_request", String(name));
  assert.equal(codeOf(await createKey(api, { name: "k", trace_mode: "full" })), "unsupported_parameter");
  for (const key of ["", 5, "k".repeat(201)]) assert.equal(codeOf(await createKey(api, { name: "k", idempotency_key: key })), "invalid_request");
  assert.deepEqual(sent, []);
  valueOf(await createKey(api, { name: "k", trace_mode: "off" }), "off is allowed");
});

test("key create: a replay (secret_returned false) is never a secret, however the answer reads", async () => {
  for (const body of [
    { ...CREATED, secret: null, secret_returned: false, replayed: true },
    { ...CREATED, secret_returned: false },
    { ...CREATED, replayed: true },
  ]) {
    const created = valueOf(await createKey(recordingApi(() => answer(200, body)).api, { name: "ci", idempotency_key: "dialog-1" }), "replay");
    assert.deepEqual([created.secret, created.replayed], [null, true], JSON.stringify(body));
  }
});

test("key create: the API's refusals keep their code with fixed text; a lost answer says what to do if it committed", async () => {
  const refusal = async (status: number, code: string) => createKey(recordingApi(() => answer(status, envelope(code))).api, { name: "k", idempotency_key: "d" });
  const suspended = await refusal(403, "org_suspended");
  assert.equal(codeOf(suspended), "org_suspended");
  assert.match(suspended.ok ? "" : suspended.error.message, /revoked but not created/);
  assert.equal(codeOf(await refusal(409, "idempotency_conflict")), "idempotency_conflict");
  assert.equal(codeOf(await refusal(401, "invalid_api_key")), "forbidden", "a dead session is 'sign in again'");
  for (const lost of [await refusal(503, "dependency_unavailable"), await createKey(recordingApi(() => { throw new Error("socket hang up"); }).api, { name: "k" })]) {
    assert.equal(codeOf(lost), "dependency_unavailable");
    assert.equal(lost.ok ? "" : lost.error.message, CREATE_UNKNOWN);
  }
  for (const r of [suspended]) assert.ok(!r.ok && !r.error.message.includes("SECRET"));
});

// ------------------------------------------------------------------------------------ key revoke

test("key revoke: DELETE /console/v1/keys/{id}; idempotent and allowed while suspended (the API's rule)", async () => {
  const { api, sent } = recordingApi(() => answer(200, { ...API_KEY, revoked_at: AT }));
  const revoked = valueOf(await revokeKey(api, KEY), "revoke");
  assert.equal(revoked.revoked_at, AT6);
  assert.deepEqual(calls(sent), [`DELETE /console/v1/keys/${KEY}`]);
});

test("key revoke: a malformed id never reaches the API; another tenant's key is not_found", async () => {
  const { api, sent } = recordingApi(() => answer(404, envelope("not_found")));
  for (const bad of ["nope", "../keys", 7, null]) assert.equal(codeOf(await revokeKey(api, bad)), "not_found");
  assert.deepEqual(sent, []);
  assert.equal(codeOf(await revokeKey(api, "c7000000-0000-4000-8000-0000000000f2")), "not_found");
  const lost = await revokeKey(recordingApi(() => { throw new Error("down"); }).api, KEY);
  assert.equal(codeOf(lost), "dependency_unavailable");
});

// ------------------------------------------------------------------------------- cross-site guard

test("same-origin: a mutation needs an Origin naming this host", () => {
  const h = (entries: Record<string, string>) => new Headers(entries);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai", host: "app.callbill.ai" })), true);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai", host: "internal:3000", "x-forwarded-host": "app.callbill.ai" })), true);
  assert.equal(sameOrigin(h({ origin: "https://evil.example", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai.evil.example", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ host: "app.callbill.ai" })), false, "no Origin is not same-origin");
  assert.equal(sameOrigin(h({ origin: "null", host: "app.callbill.ai" })), false);
  assert.equal(sameOrigin(h({ origin: "https://app.callbill.ai" })), false, "no host to compare");
});

// ----------------------------------------------------------------------------------- operator

const operator = { userId: "0a000000-0000-4000-8000-00000000000a", isOperator: true };
const ADJUST = { action: "adjust_credit", user_id: OTHER, amount: "-25.50000000", reason: "refund of a failed batch", idempotency_key: "adj-1" };

test("operator: authority, reason and idempotency key are required; the actor is the session", () => {
  assert.equal(codeOf(operatorCommand({ userId: ME, isOperator: false }, ADJUST)), "forbidden");
  const command = valueOf(operatorCommand(operator, ADJUST), "adjust");
  assert.deepEqual(command, { ...ADJUST, actor: `operator:${operator.userId}` });
  for (const [bad, why] of [
    [{ ...ADJUST, reason: "  " }, "blank reason"],
    [{ ...ADJUST, reason: undefined }, "no reason"],
    [{ ...ADJUST, idempotency_key: undefined }, "no idempotency key"],
    [{ ...ADJUST, amount: 25.5 }, "a float amount"],
    [{ ...ADJUST, amount: "0" }, "a zero adjustment"],
    [{ ...ADJUST, amount: "1e3" }, "not an exact decimal"],
    [{ ...ADJUST, unit: "USD" }, "a unit field"],
    [{ ...ADJUST, actor: "someone-else" }, "a client actor"],
    [{ ...ADJUST, user_id: "nope" }, "a malformed target"],
    [{ ...ADJUST, action: "set_balance" }, "an arbitrary balance edit"],
    [{ action: "set_suspension", org_id: MY_ORG, suspended: "yes", reason: "r", idempotency_key: "s" }, "a non-boolean"],
  ] as const) {
    assert.equal(codeOf(operatorCommand(operator, bad)), "invalid_request", why);
  }
  valueOf(operatorCommand(operator, { action: "set_suspension", org_id: MY_ORG, suspended: true, reason: "abuse", idempotency_key: "s1" }), "suspend");
  valueOf(operatorCommand(operator, { action: "revoke_key", key_id: KEY, reason: "leaked", idempotency_key: "r1" }), "revoke");
  valueOf(operatorCommand(operator, { action: "grant_initial", user_id: OTHER, reason: "backfill", idempotency_key: "g1" }), "grant");
});

test("operator: no deployed port is an explicit unavailable state, never a silent success", async () => {
  const command = valueOf(operatorCommand(operator, ADJUST), "adjust");
  const none = await runOperatorCommand(command, null);
  assert.equal(codeOf(none), "dependency_unavailable");
  const seen: unknown[] = [];
  const port: OperatorPort = {
    async run(given) {
      seen.push(given);
      return { ok: true, value: { replayed: false } };
    },
  };
  valueOf(await runOperatorCommand(command, port), "through a port");
  assert.deepEqual(seen, [command]);
  const throwing: OperatorPort = {
    async run() {
      throw new Error("connect ECONNREFUSED 10.0.0.9:5432");
    },
  };
  const failed = await runOperatorCommand(command, throwing);
  assert.equal(codeOf(failed), "dependency_unavailable");
  assert.ok(!failed.ok && !failed.error.message.includes("10.0.0.9"));
});

// ------------------------------------------------------------------------------ the action seam

const SAME = new Headers({ origin: "https://app.callbill.ai", host: "app.callbill.ai" });
const CROSS = new Headers({ origin: "https://evil.example", host: "app.callbill.ai" });

/** `consoleActions` over recording deps: which request APIs, calls and refreshes each action used. */
function seam(options: { headers: Headers; session?: { userId: string; isOperator: boolean }; reply?: (s: Sent) => Response; operator?: OperatorPort }) {
  const used: string[] = [];
  const { api, sent } = recordingApi(options.reply ?? ((s) => (s.method === "DELETE" ? answer(200, { ...API_KEY, revoked_at: AT }) : answer(201, CREATED))));
  const deps: ActionDeps = {
    headers: async () => (used.push("headers"), options.headers),
    api: async () => (used.push("api"), api),
    session: async () => (used.push("session"), options.session ?? operator),
    endSession: async () => void used.push("endSession"),
    revalidate: (path) => void used.push(`revalidate ${path}`),
    ...(options.operator ? { operator: options.operator } : {}),
  };
  return { act: consoleActions(deps), used, sent };
}

test("action seam: a cross-site request is refused before any session, call or refresh", async () => {
  const actions: [string, (act: ReturnType<typeof consoleActions>) => Promise<Result<unknown>>][] = [
    ["create", (act) => act.createKey({ name: "k" })],
    ["revoke", (act) => act.revokeKey(KEY)],
    ["operator", (act) => act.operator(ADJUST)],
    ["feedback", (act) => act.submitFeedback({ request_id: KEY, name: "thumb", value: true, idempotency_key: "f" })],
  ];
  for (const [label, call] of actions) {
    const { act, used, sent } = seam({ headers: CROSS });
    assert.equal(codeOf(await call(act)), "forbidden", label);
    assert.deepEqual([used, sent], [["headers"], []], `${label}: nothing past the Origin check`);
  }
  const { act, used } = seam({ headers: CROSS });
  await act.signOut();
  assert.deepEqual(used, ["headers"], "a cross-site sign-out ends no session");
  const same = seam({ headers: SAME });
  await same.act.signOut();
  assert.deepEqual(same.used, ["headers", "endSession"]);
});

test("action seam: the read model is refreshed only after an acknowledged change", async () => {
  const { act, used } = seam({ headers: SAME });
  valueOf(await act.createKey({ name: "k" }), "create");
  assert.deepEqual(used, ["headers", "api", "revalidate /api-keys"]);
  used.length = 0;
  valueOf(await act.revokeKey(KEY), "revoke");
  assert.deepEqual(used, ["headers", "api", "revalidate /api-keys"]);
  for (const [label, status, code] of [["refused create", 403, "forbidden"], ["failed create", 503, "dependency_unavailable"]] as const) {
    const failing = seam({ headers: SAME, reply: () => answer(status, envelope(code)) });
    assert.equal((await failing.act.createKey({ name: "k" })).ok, false, label);
    assert.ok(!failing.used.some((call) => call.startsWith("revalidate")), `${label}: no refresh`);
  }
});

test("action seam: the operator action is the session's, and with no port it is unavailable", async () => {
  const { act, used } = seam({ headers: SAME });
  assert.equal(codeOf(await act.operator(ADJUST)), "dependency_unavailable");
  assert.deepEqual(used, ["headers", "session"], "authority is re-read from the session; nothing is refreshed");
  const consumer = seam({ headers: SAME, session: { userId: ME, isOperator: false } });
  assert.equal(codeOf(await consumer.act.operator(ADJUST)), "forbidden");
});
