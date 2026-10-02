// Oracles for the transport: the session is forwarded and nothing else; every call is no-store with a
// request id; each wire format maps to its own ApiError kind; anything unreadable fails closed.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createClient, errorOf } from "../src/transport.ts";
import type { paths as Lab } from "../src/lab.ts";

type Seen = { url: string; init: RequestInit };

function fake(status: number, body: string, headers: Record<string, string> = {}) {
  const seen: Seen[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    seen.push({ url, init });
    return new Response(body, { status, headers });
  }) as unknown as typeof globalThis.fetch;
  return { seen, fetch };
}

test("a call forwards the session, a request id and no-store, and types the answer", async () => {
  const { seen, fetch } = fake(200, JSON.stringify({ data: [] }));
  const lab = createClient<Lab>({ baseUrl: "http://api.test/", fetch, requestId: () => "rid-1",
    session: () => ({ token: "eyJ0.a.b", cookie: "sb=1" }) });
  const answer = await lab.call("get", "/lab/v1/control/models", { query: { provider_org_id: "p 1" } });
  assert.deepEqual(answer, { ok: true, status: 200, data: { data: [] }, requestId: "rid-1", location: null });
  const [{ url, init }] = seen;
  assert.equal(url, "http://api.test/lab/v1/control/models?provider_org_id=p+1");
  assert.equal(init.cache, "no-store");
  assert.deepEqual(init.headers, { accept: "application/json", "x-request-id": "rid-1",
    authorization: "Bearer eyJ0.a.b", cookie: "sb=1" });
});

test("a mutation sends its JSON body, idempotency key and encoded path parameters", async () => {
  const { seen, fetch } = fake(202, JSON.stringify({ ok: 1 }), { location: "/ops/1" });
  const lab = createClient<Lab>({ baseUrl: "http://api.test", fetch });
  const answer = await lab.call("post", "/lab/v1/control/proposals/{proposal_id}/reject",
    { params: { proposal_id: "a/b" }, body: { reason: "no" }, idempotencyKey: "k1" });
  assert.equal(answer.ok && answer.location, "/ops/1");
  assert.equal(seen[0].url, "http://api.test/lab/v1/control/proposals/a%2Fb/reject");
  assert.equal(seen[0].init.body, '{"reason":"no"}');
  const headers = seen[0].init.headers as Record<string, string>;
  assert.equal(headers["idempotency-key"], "k1");
  assert.equal(headers["content-type"], "application/json");
  assert.equal(headers.authorization, undefined);
});

test("each failure format maps to its own kind; an unreadable answer is unavailable", async () => {
  const r270 = { error: { code: "not_found", message: "no", request_id: "r", retryable: false,
    field_errors: [{ field: "name", code: "invalid", message: "m" }], resource_id: "x" } };
  assert.deepEqual(errorOf(404, r270), { kind: "error", status: 404, code: "not_found", message: "no",
    requestId: "r", retryable: false, operationId: null, resourceId: "x",
    fieldErrors: [{ field: "name", code: "invalid", message: "m" }] });
  assert.deepEqual(errorOf(403, { refusal: "denied" }), { kind: "refusal", status: 403, reason: "denied" });
  assert.deepEqual(errorOf(429, { error: { message: "slow", type: "rate_limit", code: "rate_limited" } }),
    { kind: "openai", status: 429, code: "rate_limited", message: "slow" });
  assert.deepEqual(errorOf(500, { detail: [] }), { kind: "unavailable", status: 500, reason: "malformed" });
  for (const [status, text] of [[200, "<html>"], [502, "bad gateway"]] as const) {
    const client = createClient<Lab>({ baseUrl: "http://api.test", fetch: fake(status, text).fetch });
    const answer = await client.call("get", "/lab/v1/control/models");
    assert.deepEqual(!answer.ok && answer.error, { kind: "unavailable", status, reason: "malformed" });
  }
  const conflict = await createClient<Lab>({ baseUrl: "http://api.test", fetch: fake(409, JSON.stringify(r270)).fetch })
    .call("get", "/lab/v1/control/models");
  assert.equal(!conflict.ok && conflict.error.kind, "error");
  const down = createClient<Lab>({ baseUrl: "http://api.test",
    fetch: (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch });
  const answer = await down.call("get", "/lab/v1/control/models");
  assert.deepEqual(!answer.ok && answer.error, { kind: "unavailable", status: null, reason: "network" });
});
