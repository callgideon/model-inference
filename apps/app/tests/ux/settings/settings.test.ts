// node --test "tests/**/*.test.ts"
//
// UX-07 follow-up / AP-09 (04-consumer.md C-07): the consumer's data-use controls in Settings, over
// AP-07a's routes through the App transport (GET /console/v1/data-use, PUT
// /console/v1/keys/{id}/capture, DELETE /console/v1/data-grants/{id}). Failure oracles: a control that
// writes anything but one API call as the signed-in session, a retry that mints a new Idempotency-Key,
// a cross-site submission that reaches the API, a failed read rendered as "Off" or as an empty key
// list, the server's message on screen, a 401 worded as an API-key problem (the web session never
// carries a key), a suspended account offered a capture change, a revoked grant offered a withdrawal,
// and capture-off copy that implies serving stores nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { consumerApi } from "../../../lib/api/index.ts";
import { dataUseActions, readDataUse, setCapture, withdrawGrant } from "../../../lib/services/data-use/index.ts";
import { settingsModel } from "../../../app/(console)/settings/view-model.ts";

import { render, text } from "../usage/render.ts";

const APP = new URL("../../../", import.meta.url).pathname;
const source = (path: string) => readFileSync(APP + path, "utf8");

const KEY_A = "c7000000-0000-4000-8000-0000000000a1";
const KEY_B = "c7000000-0000-4000-8000-0000000000b2";
const GRANT_1 = "9a000000-0000-4000-8000-000000000001";
const GRANT_2 = "9a000000-0000-4000-8000-000000000002";
const PROVIDER = "0b000000-0000-4000-8000-0000000000bb";
const SESSION = "eyJhbGciOiJIUzI1NiJ9.session.signature";

const grant = (id: string, over: Record<string, unknown> = {}) => ({
  grant_id: id,
  version: 2,
  provider_org_id: PROVIDER,
  model_ids: ["nemostation/marlin-2b"],
  categories: ["request_content", "response_content"],
  purposes: ["provider_sharing"],
  retention_days: 30,
  effective_at: "2026-09-20T10:00:00Z",
  expires_at: null,
  revoked_at: null,
  state: "active",
  ...over,
});

const DOC = {
  consent: { version: 3, mode: "full", retention_days: 14, evaluation_consent: false, effective_at: "2026-09-20T10:00:00Z", revoked_at: null },
  keys: [
    { key_id: KEY_A, name: "production", mode: "full", effective_mode: "full" },
    { key_id: KEY_B, name: "laptop", mode: "minimal", effective_mode: "off" },
  ],
  grants: [grant(GRANT_1), grant(GRANT_2, { state: "revoked", revoked_at: "2026-09-21T09:00:00Z" })],
};

type Seen = { method: string; url: string; headers: Record<string, string>; body: unknown };
const envelope = (code: string, message = "server text that must not reach the page") => ({
  error: { code, message, request_id: "r-1", retryable: false, field_errors: [] },
});

/** The generated client over a recorded fetch that answers `answers` in order (the last repeats). */
function world(...answers: (Response | "network")[]) {
  const seen: Seen[] = [];
  const api = consumerApi({
    baseUrl: "http://api.test",
    session: () => ({ token: SESSION }),
    fetch: (async (url: string, init: RequestInit) => {
      seen.push({ method: String(init.method), url, headers: init.headers as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : null });
      const next = answers.length > 1 ? answers.shift()! : answers[0];
      if (next === "network") throw new TypeError("fetch failed");
      return next.clone();
    }) as unknown as typeof fetch,
  });
  return { api, seen };
}
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const CAPTURE = { key_id: KEY_B, mode: "full" as const, consent_version: 3, retention_days: 14, idempotency_key: "attempt-1" };

test("UXS-01 the data-use record is read once from the API as the signed-in session and shown exactly", async () => {
  const { api, seen } = world(json(200, DOC));
  const read = await readDataUse(api);
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [["GET", "http://api.test/console/v1/data-use"]]);
  assert.equal(seen[0].headers.authorization, `Bearer ${SESSION}`, "the session, never an API key");
  assert.equal(read.kind, "ready");
  assert.deepEqual(read.kind === "ready" && read.value, {
    consentVersion: 3,
    retentionDays: 14,
    keys: [
      { id: KEY_A, name: "production", mode: "full", effectiveMode: "full" },
      { id: KEY_B, name: "laptop", mode: "minimal", effectiveMode: "off" },
    ],
    grants: [
      { id: GRANT_1, providerOrgId: PROVIDER, modelIds: ["nemostation/marlin-2b"], categories: ["request_content", "response_content"], purposes: ["provider_sharing"], retentionDays: 30, expiresAt: null, revokedAt: null, state: "active" },
      { id: GRANT_2, providerOrgId: PROVIDER, modelIds: ["nemostation/marlin-2b"], categories: ["request_content", "response_content"], purposes: ["provider_sharing"], retentionDays: 30, expiresAt: null, revokedAt: "2026-09-21T09:00:00.000000Z", state: "revoked" },
    ],
  });
  // No consent decided yet: a change is submitted under the API's default retention, never a guess of 0.
  const fresh = await readDataUse(world(json(200, { ...DOC, consent: { ...DOC.consent, version: 0, mode: "off", retention_days: null } })).api);
  assert.ok(fresh.kind === "ready");
  assert.equal(fresh.value.retentionDays, 30);
  assert.equal(fresh.value.consentVersion, 0);
});

test("UXS-02 a failed read is an honest state: unavailable, forbidden or signed out - never Off, never an empty list", async () => {
  const kind = async (...answers: (Response | "network")[]) => (await readDataUse(world(...answers).api)).kind;
  assert.equal(await kind(json(503, envelope("dependency_unavailable"))), "unavailable");
  assert.equal(await kind("network"), "unavailable");
  // The routes are off (CONSOLE_DATA_USE): FastAPI's bare 404, or the client fake's envelope.
  assert.equal(await kind(json(404, { detail: "Not Found" })), "unavailable");
  assert.equal(await kind(json(404, envelope("not_found"))), "unavailable");
  assert.equal(await kind(json(200, { ...DOC, keys: [{ ...DOC.keys[0], mode: "everything" }] })), "unavailable", "an unknown mode is not shown");
  assert.equal(await kind(json(200, { ...DOC, keys: undefined })), "unavailable");
  assert.equal(await kind(json(200, { ...DOC, grants: [grant(GRANT_1, { state: "pending" })] })), "unavailable", "an unknown grant state is not shown");
  assert.equal(await kind(json(403, envelope("forbidden"))), "forbidden");
  // The routes refuse an API key with 401 invalid_api_key; the App sends only the session's bearer, so
  // a 401 here can only be a session that ended.
  assert.equal(await kind(json(401, envelope("invalid_api_key"))), "signed_out");
});

test("UXS-03 a capture change is one PUT with the submission's Idempotency-Key, and a retry sends the same key", async () => {
  const changed = { ...DOC, consent: { ...DOC.consent, version: 4 }, keys: [DOC.keys[0], { ...DOC.keys[1], mode: "full", effective_mode: "full" }] };
  const { api, seen } = world(json(503, envelope("dependency_unavailable")), json(200, changed));
  const first = await setCapture(api, CAPTURE);
  assert.equal(first.ok, false);
  const retry = await setCapture(api, CAPTURE);
  assert.ok(retry.ok);
  assert.equal(retry.value.consentVersion, 4);
  assert.equal(retry.value.keys[1].mode, "full");
  assert.equal(seen.length, 2);
  for (const call of seen) {
    assert.equal(call.method, "PUT");
    assert.equal(call.url, `http://api.test/console/v1/keys/${KEY_B}/capture`);
    assert.equal(call.headers["idempotency-key"], "attempt-1");
    assert.equal(call.headers.authorization, `Bearer ${SESSION}`);
    // Capture implies none of the other purposes: evaluation consent is never given from here.
    assert.deepEqual(call.body, { mode: "full", consent_version: 3, retention_days: 14, evaluation_consent: false });
  }
});

test("UXS-04 a malformed capture submission is refused before any call", async () => {
  for (const bad of [
    { ...CAPTURE, mode: "everything" },
    { ...CAPTURE, key_id: "not-a-key" },
    { ...CAPTURE, idempotency_key: "" },
    { ...CAPTURE, idempotency_key: undefined },
    { ...CAPTURE, consent_version: -1 },
    { ...CAPTURE, consent_version: 1.5 },
    { ...CAPTURE, retention_days: 0 },
    { ...CAPTURE, retention_days: 91 },
    { ...CAPTURE, org_id: "0a000000-0000-4000-8000-0000000000aa" },
    { ...CAPTURE, evaluation_consent: true },
    null,
  ]) {
    const { api, seen } = world(json(200, DOC));
    const result = await setCapture(api, bad);
    assert.equal(result.ok, false, JSON.stringify(bad));
    assert.equal(!result.ok && result.error.code, "invalid_request", JSON.stringify(bad));
    assert.equal(seen.length, 0, `${JSON.stringify(bad)} reached the API`);
  }
});

test("UXS-05 a refused change says why in the App's words; an unconfirmed one says retrying is safe", async () => {
  const refusal = async (answer: Response | "network") => {
    const result = await setCapture(world(answer).api, CAPTURE);
    assert.equal(result.ok, false);
    return !result.ok ? result.error : { code: "", message: "" };
  };
  const conflict = await refusal(json(409, envelope("state_conflict")));
  assert.equal(conflict.code, "state_conflict");
  assert.match(conflict.message, /changed since this page loaded; reload/);
  const suspended = await refusal(json(403, envelope("org_suspended")));
  assert.equal(suspended.code, "org_suspended");
  assert.match(suspended.message, /suspended/);
  const owner = await refusal(json(403, envelope("forbidden")));
  assert.equal(owner.code, "forbidden");
  assert.match(owner.message, /owner/);
  const ended = await refusal(json(401, envelope("invalid_api_key")));
  assert.equal(ended.code, "forbidden");
  assert.match(ended.message, /session has ended; sign in again/);
  assert.equal((await refusal(json(404, envelope("not_found")))).message, "no such key for this account");
  for (const lost of [json(503, envelope("dependency_unavailable")), "network" as const, json(500, { oops: true })]) {
    const error = await refusal(lost);
    assert.equal(error.code, "dependency_unavailable");
    assert.match(error.message, /not confirmed.*try again/);
  }
  for (const answer of [json(409, envelope("state_conflict")), json(403, envelope("forbidden")), json(503, envelope("dependency_unavailable"))]) {
    assert.doesNotMatch((await refusal(answer)).message, /server text/);
  }
});

test("UXS-06 withdrawing a grant is one DELETE with its Idempotency-Key and answers the revoked grant", async () => {
  const revoked = grant(GRANT_1, { state: "revoked", revoked_at: "2026-09-22T08:00:00Z", version: 3 });
  const { api, seen } = world(json(200, revoked));
  const result = await withdrawGrant(api, { grant_id: GRANT_1, idempotency_key: "attempt-2" });
  assert.ok(result.ok);
  assert.equal(result.value.state, "revoked");
  assert.equal(result.value.revokedAt, "2026-09-22T08:00:00.000000Z");
  assert.deepEqual(seen.map((s) => [s.method, s.url, s.headers["idempotency-key"], s.body]), [
    ["DELETE", `http://api.test/console/v1/data-grants/${GRANT_1}`, "attempt-2", null],
  ]);
  for (const bad of [{ grant_id: "x", idempotency_key: "k" }, { grant_id: GRANT_1, idempotency_key: "" }, { grant_id: GRANT_1, idempotency_key: "k", provider_org_id: PROVIDER }]) {
    const quiet = world(json(200, revoked));
    assert.equal((await withdrawGrant(quiet.api, bad)).ok, false);
    assert.equal(quiet.seen.length, 0);
  }
  const missing = await withdrawGrant(world(json(404, envelope("not_found"))).api, { grant_id: GRANT_1, idempotency_key: "k" });
  assert.equal(!missing.ok && missing.error.message, "no such grant for this account");
});

test("UXS-07 the settings actions refuse a cross-site submission and refresh Settings only after an acknowledged change", async () => {
  const run = async (origin: string, ...answers: Response[]) => {
    const { api, seen } = world(...answers);
    const refreshed: string[] = [];
    const actions = dataUseActions({
      headers: async () => new Headers({ origin, host: "app.example" }),
      api: async () => api,
      revalidate: (path) => refreshed.push(path),
    });
    const capture = await actions.setCapture(CAPTURE);
    const withdraw = await actions.withdrawGrant({ grant_id: GRANT_1, idempotency_key: "attempt-3" });
    return { capture, withdraw, seen, refreshed };
  };
  const cross = await run("https://evil.example", json(200, DOC));
  assert.equal(cross.seen.length, 0, "nothing reaches the API");
  assert.equal(!cross.capture.ok && cross.capture.error.code, "forbidden");
  assert.equal(!cross.withdraw.ok && cross.withdraw.error.code, "forbidden");
  assert.deepEqual(cross.refreshed, []);
  const ok = await run("https://app.example", json(200, DOC), json(200, grant(GRANT_1, { state: "revoked", revoked_at: "2026-09-22T08:00:00Z" })));
  assert.ok(ok.capture.ok && ok.withdraw.ok);
  assert.equal(ok.seen.length, 2);
  assert.deepEqual(ok.refreshed, ["/settings", "/settings"]);
  const refused = await run("https://app.example", json(409, envelope("state_conflict")));
  assert.deepEqual(refused.refreshed, []);
});

// ---------------------------------------------------------------------------------------------------
// The section (04-consumer.md C-07), rendered from fixtures with inert actions
// ---------------------------------------------------------------------------------------------------

const SECTION = "app/(console)/settings/data-use.tsx";
const inert = async () => ({ ok: false as const, error: { code: "dependency_unavailable" as const, message: "fixture" } });
const section = async (read: unknown, suspended = false) =>
  render(SECTION, "DataUseSection", { read, suspended, save: inert, withdraw: inert });
const ready = async () => {
  const read = await readDataUse(world(json(200, DOC)).api);
  assert.ok(read.kind === "ready");
  return read;
};

test("UXS-08 a ready record shows each key's capture choice, what it records, and the grants with withdrawal only where active", async () => {
  const html = await section(await ready());
  const shown = text(html);
  assert.match(shown, /production/);
  assert.match(shown, /laptop/);
  // Each key's own choice is the selected option; the laptop key records nothing yet (effective off).
  const selected = [...html.matchAll(/<option value="(off|minimal|full)" selected="">/g)].map((m) => m[1]);
  assert.deepEqual(selected, ["full", "minimal"]);
  assert.match(shown, /laptop[^]*Recording now: Off/);
  assert.doesNotMatch(shown, /production[^]*Recording now[^]*laptop/, "a key recording as chosen carries no note");
  assert.match(shown, /Captured content is kept for 14 days/);
  assert.match(shown, /applies to requests sent after you save/i);
  // Truthful retention: capture off is not "nothing stored".
  assert.match(shown, /Turning capture off does not change what we store to run a request/);
  assert.doesNotMatch(shown, /\bZDR\b|zero (data )?retention|never stor|nothing is stored|not stored/i);
  assert.equal((html.match(/>Save</g) ?? []).length, 2, "one Save per key");
  assert.match(shown, /Provider 0b000000-0000-4000-8000-0000000000bb/);
  assert.match(shown, /provider sharing/);
  assert.equal((html.match(/>Withdraw</g) ?? []).length, 1, "only the active grant can be withdrawn");
  assert.match(shown, /Withdrawn 2026-09-21 09:00 UTC/);
  assert.doesNotMatch(html, /type="checkbox"|role="switch"/, "no toggle that saves on change");
});

test("UXS-09 every other state is honest: unavailable, forbidden, signed out, suspended, no keys, no grants", async () => {
  const unavailable = text(await section({ kind: "unavailable" }));
  assert.match(unavailable, /Data-use settings are unavailable right now/);
  assert.match(unavailable, /Try again/);
  assert.doesNotMatch(unavailable, /\bOff\b|Save|Withdraw|no (active )?keys|not granted/i, "a failed read shows no choice and no empty list");
  const forbidden = text(await section({ kind: "forbidden" }));
  assert.match(forbidden, /Only the account's owner/);
  assert.doesNotMatch(forbidden, /Save|Withdraw/);
  const html = await section({ kind: "signed_out" });
  assert.match(text(html), /Your session has ended\. Sign in again/);
  assert.match(html, /href="\/login"/);
  assert.doesNotMatch(text(html), /API key|invalid/i, "a web session is never told about API keys");
  // Suspended: capture cannot change (R33), a grant can still be withdrawn.
  const frozen = await section(await ready(), true);
  assert.match(text(frozen), /This account is suspended: capture cannot be changed\. You can still withdraw a grant\./);
  assert.equal((frozen.match(/<button[^>]*disabled=""[^>]*>Save</g) ?? []).length, 2);
  assert.equal((frozen.match(/<select[^>]*disabled=""/g) ?? []).length, 2);
  assert.doesNotMatch(frozen, /<button[^>]*disabled=""[^>]*>Withdraw</);
  const live = await section(await ready());
  assert.doesNotMatch(live, /disabled=""/);
  const empty = await readDataUse(world(json(200, { ...DOC, keys: [], grants: [] })).api);
  const none = text(await section(empty));
  assert.match(none, /You have no active keys\. Capture is chosen per key/);
  assert.match(none, /You have not granted any provider use of your data\./);
});

test("UXS-10 Settings has a loading state in the page's order", async () => {
  const html = await render("app/(console)/settings/loading.tsx", "default", {});
  assert.match(html, /aria-busy="true"/);
  assert.match(text(html), /Loading settings/);
});

test("UXS-11 the page reads data use through the request's client and wires the settings actions; the form keeps its key until a change is acknowledged", () => {
  const page = source("app/(console)/settings/page.tsx");
  assert.match(page, /context\.state === "ready" \? await readDataUse\(\(await apiSource\(\)\)\.api\) : null/);
  assert.match(page, /<DataUseSection read=\{dataUse\} suspended=\{context\.account\.suspended\} save=\{setKeyCapture\} withdraw=\{withdrawDataGrant\} \/>/);
  const actions = source("app/(console)/settings/actions.ts");
  assert.match(actions, /^"use server";/);
  assert.match(actions, /api: async \(\) => \(await apiSource\(\)\)\.api/);
  assert.match(actions, /revalidate: \(path\) => revalidatePath\(path\)/);
  const controls = source("app/(console)/settings/data-use-controls.tsx");
  assert.match(controls, /^"use client";/);
  assert.equal(controls.split("setKey(result.ok ? newKey() : key);").length - 1, 2, "both forms rotate only after an acknowledged change");
  assert.equal(controls.split("idempotency_key: key").length - 1, 2, "both forms submit the key they hold");
});

test("UXS-12 the privacy facts say capture is off unless turned on for a key and never stands in for serving retention", () => {
  const model = settingsModel({ state: "ready", account: { email: "a@example.com", suspended: false } } as never);
  const capture = model.privacy.find((r) => /trace capture/i.test(r.title));
  assert.ok(capture);
  assert.equal(capture.status, "Off unless you turn it on");
  assert.match(capture.detail, /only after the account owner turns it on for that key/);
  assert.match(capture.detail, /It does not change what we store to run a request \(above\)\./);
  assert.doesNotMatch(capture.detail, /cannot be turned on/);
  const sharing = model.privacy.find((r) => /training/i.test(r.title));
  assert.equal(sharing?.status, "Off unless you grant it");
  assert.match(sharing?.detail ?? "", /Signing up grants no permission for any of these/);
});
