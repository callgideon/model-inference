// V1M / WR-LAB-API-5 (TRACE-TENANT, LAB-ACCESS): the Lab's adapter over lab-api's provider trace read
// (R176, apps/infrx-api/infrx/gateway/routes/lab_traces.py). The tenancy itself is the route's (provider
// A vs B, consumer-only, revoked grant, T3): these cases pin what the Lab sends and how it reads the
// answer; stack.test.ts runs the same adapter against the real route on lab-v1m + ClickHouse.
import assert from "node:assert/strict";
import test from "node:test";
import { httpTraces, LIST_LIMIT, offlineTraces } from "../../../lib/services/traces/port.ts";
import type { Actor } from "../../../components/traces/detail/port.ts";

const A = "a0000001-0000-4000-8000-000000000001";
const REQ = "5c000000-0000-4000-8000-0000000000f1";
const ORG = "0a000000-0000-4000-8000-00000000000a";
const actor: Actor = { providerId: A, role: "developer" };
const meta = {
  request_id: REQ, started_at: "2026-09-27T10:00:00+00:00", completed_at: null, mode: "full", loss_reason: "none",
  serving_version_id: "sv-1", model_revision: "acme-7b@r2", rate_card_version: "rc-3", policy_version: null, model_id: "acme-7b", access: "metadata",
};
const granted = { ...meta, access: "content", grantor_org_id: ORG, grant_ref: "grant-1", content_complete: true, content_bytes: 812, content_available: true };

type Call = { url: string; init: RequestInit };
function server(status: number, body: unknown, token: string | null = "eyJ0.tok.sig") {
  const calls: Call[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
  }) as unknown as typeof globalThis.fetch;
  return { calls, port: httpTraces({ baseUrl: "https://api.example/", token: async () => token, fetch }) };
}

test("V1M-A01 reads as the session's workspace with the user's own token: the list and one request", async () => {
  const s = server(200, { data: [meta], next_cursor: "Y3Vy" });
  assert.deepEqual(await s.port.list(actor, null), { ok: true, value: { items: [meta], next_cursor: "Y3Vy" } });
  await s.port.list(actor, "Y3Vy");
  await s.port.detail(actor, "a b/../x");
  assert.deepEqual(s.calls.map((c) => c.url), [
    `https://api.example/lab/v1/traces?provider_org_id=${A}&limit=${LIST_LIMIT}`,
    `https://api.example/lab/v1/traces?provider_org_id=${A}&limit=${LIST_LIMIT}&cursor=Y3Vy`,
    `https://api.example/lab/v1/traces/a%20b%2F..%2Fx?provider_org_id=${A}`,
  ]);
  for (const { init } of s.calls) {
    assert.equal(new Headers(init.headers).get("authorization"), "Bearer eyJ0.tok.sig");
    assert.equal(init.cache, "no-store");
  }
  assert.equal(LIST_LIMIT, 50);
});

test("V1M-A02 refusals map to denied, not_found or unavailable, and no token sends nothing", async () => {
  for (const [status, reason] of [[401, "denied"], [403, "denied"], [404, "not_found"], [422, "unavailable"], [500, "unavailable"], [503, "unavailable"]] as const) {
    const s = server(status, { refusal: "x" });
    assert.deepEqual(await s.port.list(actor, null), { ok: false, reason }, String(status));
    assert.deepEqual(await s.port.detail(actor, REQ), { ok: false, reason }, String(status));
  }
  const tokenless = server(200, { data: [], next_cursor: null }, null);
  assert.deepEqual(await tokenless.port.list(actor, null), { ok: false, reason: "unavailable" });
  assert.deepEqual(await tokenless.port.detail(actor, REQ), { ok: false, reason: "unavailable" });
  assert.equal(tokenless.calls.length, 0);
  const down = httpTraces({ baseUrl: "https://api.example", token: async () => "t", fetch: (async () => { throw new TypeError("fetch failed"); }) as never });
  assert.deepEqual(await down.list(actor, null), { ok: false, reason: "unavailable" });
  const thrown = httpTraces({ baseUrl: "https://api.example", token: async () => { throw new Error("cookies outside a request"); } });
  assert.deepEqual(await thrown.list(actor, null).catch(() => "threw"), { ok: false, reason: "unavailable" });
  const off = offlineTraces();
  assert.deepEqual([await off.list(actor, null), await off.detail(actor, REQ)], [{ ok: false, reason: "unavailable" }, { ok: false, reason: "unavailable" }]);
});

test("V1M-A03 only the route's named fields are kept: a metadata row never carries an organization, key, size or content", async () => {
  const leaky = { ...meta, org_id: ORG, key_id: "k-customer", content_bytes: 9, content: "secret prompt", grantor_org_id: ORG };
  const s = server(200, { data: [leaky, granted], next_cursor: null });
  const page = await s.port.list(actor, null);
  assert.ok(page.ok);
  assert.deepEqual(page.value.items, [meta, granted]);
  const one = server(200, { ...granted, content: "text", extra: 1 });
  assert.deepEqual(await one.port.detail(actor, REQ), { ok: true, value: granted });
});

test("V1M-A04 an answer that is not the route's shape is unavailable, never a partial record", async () => {
  const bad = [
    "not json", "null", [], { data: "x", next_cursor: null }, { data: [meta], next_cursor: 5 }, { data: [{ ...granted, access: "all" }], next_cursor: null },
    { data: [{ ...meta, model_id: null }], next_cursor: null },
    { data: [{ ...meta, started_at: 1 }], next_cursor: null }, { data: [{ ...meta, completed_at: undefined }], next_cursor: null },
    { data: [{ ...granted, content_bytes: "812" }], next_cursor: null }, { data: [{ ...meta, rate_card_version: 3 }], next_cursor: null },
  ];
  for (const body of bad) {
    const answer = await server(200, body).port.list(actor, null).catch(() => "threw");
    assert.deepEqual(answer, { ok: false, reason: "unavailable" }, JSON.stringify(body));
  }
  // A granted detail must name the grant it was read under (WR-LAB-API-6): C2 binds its content ref to it.
  const unnamed: Record<string, unknown> = { ...granted };
  delete unnamed.grant_ref;
  assert.deepEqual(await server(200, unnamed).port.detail(actor, REQ).catch(() => "threw"), { ok: false, reason: "unavailable" });
  assert.deepEqual(await server(200, "not json").port.detail(actor, REQ).catch(() => "threw"), { ok: false, reason: "unavailable" });
  // The list reads no content, so a granted row without the ref still lists (the ref is never used there).
  const listed = await server(200, { data: [unnamed], next_cursor: null }).port.list(actor, null);
  assert.ok(listed.ok && listed.value.items[0].access === "content" && (listed.value.items[0] as { grant_ref: string }).grant_ref === "");
});
