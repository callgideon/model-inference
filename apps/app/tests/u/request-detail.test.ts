// node --test "tests/**/*.test.ts"
//
// U4: the owned request detail and its result lifecycle (USER-RESULTS, RESULT-EXPIRY,
// CONSOLE-TENANT). The reads (`usage/[requestId]/request-reads.ts`) run through the generated client
// over a recording `fetch` (AP-09: `GET /console/v1/requests/{id}[/result]`); the view model
// (`request-view-model.ts`) is pure. tests/ap02 proves the API's answers on real PostgreSQL.
//
// Failure oracles, one per seam: a read that trusts a caller-named or malformed id, a result served
// for a request the API would withhold, a refusal shown as "not ready" (or a zero), a response a
// cache may keep, a client that keeps content past its persisted expiry, a poll that never stops,
// a retry that submits another paid inference, and a gate that serves fixtures in production.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { answer, defaultWorld, envelope, fakeConsoleApi, recordingApi, type Sent } from "../../lib/fake-api.ts";
import { jobOf, type ConsumerJob } from "../../app/(console)/billing/credit-reads.ts";
import { apiRequestReads, resultResponse, type ResultRead } from "../../app/(console)/usage/[requestId]/request-reads.ts";
import {
  MAX_POLLS,
  RETRY_GUIDANCE,
  clientResultRead,
  expiryDelayMs,
  pollDelayMs,
  pollLoop,
  pollsFor,
  readResult,
  requestDetailModel,
  resultAccessOf,
  watchExpiry,
  watchResult,
  type Schedule,
  type Shown,
} from "../../app/(console)/usage/[requestId]/request-view-model.ts";

const app = join(dirname(fileURLToPath(import.meta.url)), "../..");
const route = (file: string) => readFileSync(join(app, "app/(console)/usage/[requestId]", file), "utf8");
/** A route file's code with its comments removed, so a source assertion never matches prose. */
const code = (file: string) => route(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

/** A fake timer and clock: `fire` runs the earliest armed timer, moving the clock to it. */
function fakeTimers(start = 0) {
  let clock = start;
  const armed: { at: number; ms: number; run: () => void; live: boolean }[] = [];
  const schedule: Schedule = (run, ms) => {
    const timer = { at: clock + ms, ms, run, live: true };
    armed.push(timer);
    return () => {
      timer.live = false;
    };
  };
  const live = () => armed.filter((t) => t.live).sort((a, b) => a.at - b.at);
  const fire = () => {
    const [next] = live();
    next.live = false;
    clock = next.at;
    next.run();
  };
  return { schedule, now: () => clock, set: (at: number) => (clock = at), live, fire };
}

const ID = "b1000000-0000-4000-8000-000000000001";
const fixtureJobs = defaultWorld().requests.map(jobOf);

/** A request summary as infrx-api renders it: money as `{amount, unit}`, instants `+00:00`. */
function row(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    request_id: ID,
    created_at: "2026-09-20T11:00:00+00:00",
    requested_model: "nemostation/marlin-2b",
    model_revision: "nemostation/marlin-2b@2026-09-01",
    execution_mode: "async",
    state: "succeeded",
    outcome_cause: "completed",
    accounting_regime: "credit",
    settlement_state: "settled",
    usage_certainty: "authoritative",
    prompt_tokens: 1200,
    completion_tokens: 340,
    hold: { amount: "5.00000000", unit: "CREDIT" },
    hold_state: "settled",
    charged: { amount: "1.23456789", unit: "CREDIT" },
    result_access: "available",
    result_expires_at: "2026-09-21T11:00:05+00:00",
    ...over,
  };
}

/** Answers each request from `script` by route (the summary, or its `/result`), in order. */
function recording(script: { job?: (Response | Error)[]; result?: (Response | Error)[] }) {
  return recordingApi((sent: Sent) => {
    const queue = sent.path.endsWith("/result") ? script.result : script.job;
    const next = queue?.shift();
    if (next === undefined) throw new Error(`unscripted call to ${sent.path}`);
    if (next instanceof Error) throw next;
    return next;
  });
}

function job(over: Partial<ConsumerJob> = {}): ConsumerJob {
  return { ...fixtureJobs[0], ...over };
}

const T = {
  job: "U4-R01 the job read asks the API for exactly this request (canonical id), as the session",
  foreign: "U4-R02 a foreign, unknown, mismatched or malformed id is 'not found', and a malformed one never reaches the API",
  jobErrors: "U4-R03 a failed job read is a typed failure, never an empty 'not found'",
  gate: "U4-R04 the result is served only when the API would serve it: the job read gates the content read",
  result: "U4-R05 each content refusal keeps its meaning: not found, not ready, expired, signed out, unavailable",
  response: "U4-R06 every result response is private and no-store, and only a ready one carries content",
  access: "U4-V01 result access is the contract's read classification of the persisted fields",
  detail: "U4-V02 the detail shows exact charge state in the job's own unit; a hold or unknown usage is never a charge",
  failure: "U4-V03 a failure is explained with sanitized, actionable copy; the phase is waiting, running or finished",
  expiry: "U4-V04 content expiry keeps the request's metadata and charge but removes content access",
  poll: "U4-V05 polling backs off, is bounded, and runs only while the request (or a retryable read) is unfinished",
  client: "U4-V06 the browser trusts only a same-origin JSON answer; a redirect or 401 is a signed-out state",
  timer: "U4-V07 the browser drops content at the persisted expiry and never before it is due",
  loop: "U4-V09 the poll loop refreshes on the backoff, stops after MAX_POLLS, and unmount cancels it",
  watch: "U4-V10 an open page drops content when the persisted expiry is due, never before, re-arming past the timer ceiling",
  reads: "U4-V11 the result is read on mount and again on a back/forward restore (content hidden first); navigation aborts it",
  fetch: "U4-V12 the browser's result fetch is same-origin and no-store, and an abandoned read shows nothing",
  retry: "U4-V08 no control submits another inference; retry guidance says a rerun is a new, separately charged request",
  source: "U4-G01 the request fake is reachable only through the development preview gate",
  surface: "U4-S01 content stays out of the page payload, browser storage and logs; the route answers no-store",
  wiring: "U4-S02 the client components run the tested drivers, no fetch bypasses them, and the page mounts the poller only when it polls",
};

test(T.job, async () => {
  const { api, sent } = recording({ job: [answer(200, row())] });
  const read = await apiRequestReads(api).job(ID.toUpperCase());
  assert.equal(read.ok, true);
  assert.equal(read.ok && read.value?.requestId, ID);
  assert.equal(read.ok && read.value?.charged, "1.23456789");
  assert.deepEqual(sent.map((s) => `${s.method} ${s.path}`), [`GET /console/v1/requests/${ID}`]);
});

test(T.foreign, async () => {
  // Another tenant's id and an unknown one answer the same: the API's 404.
  const empty = recording({ job: [answer(404, envelope("not_found"))] });
  assert.deepEqual(await apiRequestReads(empty.api).job(ID), { ok: true, value: null });

  // A summary for a different request is never shown as this one.
  const mismatched = recording({ job: [answer(200, row({ request_id: "b1000000-0000-4000-8000-000000000002" }))] });
  assert.deepEqual(await apiRequestReads(mismatched.api).job(ID), { ok: true, value: null });

  for (const bad of ["", "abc", `${ID}x`, `${ID}' or '1'='1`, "../billing", ID.replaceAll("-", "")]) {
    const none = recording({});
    const reads = apiRequestReads(none.api);
    assert.deepEqual(await reads.job(bad), { ok: true, value: null }, bad);
    assert.deepEqual(await reads.result(bad), { state: "not_found" }, bad);
    assert.equal(none.sent.length, 0, `${bad} reached the API`);
  }
});

test(T.jobErrors, async () => {
  const cases: [Response | Error, string][] = [
    [new Error("socket hang up"), "dependency_unavailable"],
    [answer(503, envelope("dependency_unavailable")), "dependency_unavailable"],
    [answer(401, envelope("invalid_api_key")), "forbidden"],
    [answer(200, row({ charged: { amount: "1.234567891", unit: "CREDIT" } })), "internal_error"], // inexact money is refused
    [answer(200, row({ charged: { amount: "1.23456789", unit: "USD" } })), "internal_error"], // a CREDIT request relabelled USD
  ];
  for (const [reply, code] of cases) {
    const read = await apiRequestReads(recording({ job: [reply] }).api).job(ID);
    assert.equal(read.ok, false);
    assert.equal(!read.ok && read.error.code, code);
  }
});

test(T.gate, async () => {
  // Available: the summary read, then the content read.
  const ready = recording({ job: [answer(200, row())], result: [answer(200, { request_id: ID, text: "a sop verdict", expires_at: null })] });
  assert.deepEqual(await apiRequestReads(ready.api).result(ID), { state: "ready", text: "a sop verdict" });
  assert.deepEqual(ready.sent.map((s) => s.path), [`/console/v1/requests/${ID}`, `/console/v1/requests/${ID}/result`]);

  // The API withholds these (F2C.b read_outcome), so the content read is never made.
  const withheld: [Record<string, unknown>, ResultRead][] = [
    [{ state: "running", outcome_cause: null, settlement_state: null, usage_certainty: null, prompt_tokens: null, completion_tokens: null, hold_state: "held", charged: null, result_access: "pending", result_expires_at: null }, { state: "pending" }],
    [{ settlement_state: "held_unknown", usage_certainty: "unknown", prompt_tokens: null, completion_tokens: null, hold_state: "unknown", charged: null, result_access: "held_unknown" }, { state: "withheld" }],
    [{ result_access: "expired" }, { state: "expired" }],
    [{ result_access: "unavailable", result_expires_at: null }, { state: "unavailable" }],
    [{ state: "failed", outcome_cause: "engine_error", settlement_state: "released_platform_absorbed", charged: null, result_access: "no_result", result_expires_at: null }, { state: "no_result" }],
  ];
  for (const [over, expected] of withheld) {
    const { api, sent } = recording({ job: [answer(200, row(over))] });
    assert.deepEqual(await apiRequestReads(api).result(ID), expected, JSON.stringify(over));
    assert.equal(sent.length, 1, `content was read for ${expected.state}`);
  }

  const foreign = recording({ job: [answer(404, envelope("not_found"))] });
  assert.deepEqual(await apiRequestReads(foreign.api).result(ID), { state: "not_found" });
  assert.equal(foreign.sent.length, 1);

  const down = recording({ job: [new Error("ECONNRESET")] });
  assert.deepEqual(await apiRequestReads(down.api).result(ID), { state: "unavailable" });

  // A session the API refuses has ended: the reader signs in again.
  const signedOut = recording({ job: [answer(401, envelope("invalid_api_key"))] });
  assert.deepEqual(await apiRequestReads(signedOut.api).result(ID), { state: "signed_out" });
});

test(T.result, async () => {
  // Between the two reads the request may expire, or the session may end: the content read decides.
  const cases: [Response | Error, ResultRead][] = [
    [answer(404, envelope("not_found")), { state: "not_found" }],
    [answer(409, envelope("result_pending")), { state: "pending" }],
    [answer(410, envelope("result_expired")), { state: "expired" }],
    [answer(401, envelope("invalid_api_key")), { state: "signed_out" }],
    [answer(422, envelope("something_else")), { state: "unavailable" }],
    [answer(503, envelope("dependency_unavailable")), { state: "unavailable" }],
    [new Error("fetch failed"), { state: "unavailable" }],
    [answer(200, null), { state: "unavailable" }],
    [answer(200, { request_id: ID, text: 7 }), { state: "unavailable" }],
    [answer(200, { request_id: ID, text: "", expires_at: null }), { state: "ready", text: "" }],
  ];
  for (const [reply, expected] of cases) {
    const { api } = recording({ job: [answer(200, row())], result: [reply] });
    assert.deepEqual(await apiRequestReads(api).result(ID), expected, String(expected.state));
  }
});

test(T.response, async () => {
  const states: [ResultRead, number][] = [
    [{ state: "ready", text: "the verdict" }, 200],
    [{ state: "pending" }, 409],
    [{ state: "withheld" }, 409],
    [{ state: "no_result" }, 404],
    [{ state: "not_found" }, 404],
    [{ state: "expired" }, 410],
    [{ state: "unavailable" }, 503],
    [{ state: "signed_out" }, 401],
  ];
  for (const [read, status] of states) {
    const response = resultResponse(read);
    assert.equal(response.status, status, read.state);
    assert.equal(response.headers.get("cache-control"), "private, no-store, max-age=0", read.state);
    assert.match(response.headers.get("content-type") ?? "", /^application\/json/);
    const body = await response.json();
    assert.deepEqual(body, read, read.state);
    if (read.state !== "ready") assert.equal("text" in body, false);
  }
});

test(T.access, () => {
  const jobs = fixtureJobs;
  const byId = (n: number) => jobs.find((j) => j.requestId.endsWith(`${n}`)) as ConsumerJob;
  assert.equal(resultAccessOf(byId(1)), "available");
  assert.equal(resultAccessOf(byId(2)), "unavailable", "a success with no persisted expiry is never available");
  assert.equal(resultAccessOf(byId(3)), "pending");
  assert.equal(resultAccessOf(byId(4)), "held_unknown");
  assert.equal(resultAccessOf(byId(5)), "no_result");
  assert.equal(resultAccessOf(byId(7)), "no_result", "a cancelled request has no result, even with usage");
  // The database's `result_available` (its own clock) decides available vs expired, not ours.
  assert.equal(resultAccessOf(job({ resultAvailable: false })), "expired");
  assert.equal(resultAccessOf(job({ resultAvailable: true, resultExpiresAt: "2000-01-01T00:00:00.000000Z" })), "available");
  // A success whose usage is not authoritative is not served (F2C.b), whatever the flag says.
  assert.equal(resultAccessOf(job({ usageCertainty: "unknown", promptTokens: null, completionTokens: null })), "no_result");
});

test(T.detail, () => {
  const settled = requestDetailModel({ ok: true, value: job() });
  assert.equal(settled.kind, "ready");
  if (settled.kind !== "ready") return;
  assert.equal(settled.value.charge.label, "Charged");
  assert.equal(settled.value.charge.amount, "1.23456789 credits");
  assert.equal(settled.value.requestId, ID);
  assert.equal(settled.value.created, "2026-09-20 11:00 UTC");
  assert.equal(settled.value.model, "nemostation/marlin-2b");
  assert.equal(settled.value.revision, "nemostation/marlin-2b@2026-09-01");
  assert.equal(settled.value.tokens.input, "1,200");

  const running = requestDetailModel({ ok: true, value: job({ state: "running", outcomeCause: null, settlementState: null, holdState: "held", charged: null, resultAvailable: false, resultExpiresAt: null }) });
  assert.equal(running.kind === "ready" && running.value.charge.amount, null, "a hold is not a charge");
  assert.equal(running.kind === "ready" && running.value.charge.held, "5.00 credits");

  const unknown = requestDetailModel({ ok: true, value: fixtureJobs[3] });
  assert.equal(unknown.kind === "ready" && unknown.value.charge.amount, null, "unknown usage is never a zero charge");
  assert.equal(unknown.kind === "ready" && unknown.value.charge.label, "Awaiting reconciliation");

  const legacy = requestDetailModel({ ok: true, value: fixtureJobs[7] });
  assert.equal(legacy.kind === "ready" && legacy.value.charge.amount, "$0.0001966 USD");
  assert.equal(legacy.kind === "ready" && legacy.value.unit, "legacy USD");

  for (const model of [settled, running, unknown]) {
    assert.doesNotMatch(JSON.stringify(model), /\$/, "no dollar sign on a CREDIT request");
  }

  assert.equal(requestDetailModel({ ok: true, value: null }).kind, "empty");
  const failed = requestDetailModel({ ok: false, error: { code: "dependency_unavailable", message: "down" } });
  assert.equal(failed.kind, "error");
  assert.equal(failed.kind === "error" && failed.recovery, "retry");
});

test(T.failure, () => {
  const phase = (state: string) => {
    const m = requestDetailModel({ ok: true, value: job({ state, outcomeCause: null, settlementState: null, resultAvailable: false }) });
    return m.kind === "ready" ? m.value.phase : null;
  };
  assert.equal(phase("preparing"), "waiting");
  assert.equal(phase("queued"), "waiting");
  assert.equal(phase("running"), "running");
  assert.equal(phase("mystery"), "unknown");

  const causes = ["client_cancelled", "client_disconnected", "sync_deadline", "queue_wait_expired", "deadline_exceeded", "invalid_media", "preparation_failed", "engine_error", "engine_incomplete", "lost_after_publication", "journal_write_failed", "retries_exhausted", "platform_error"];
  for (const cause of causes) {
    const m = requestDetailModel({ ok: true, value: job({ state: "failed", outcomeCause: cause, settlementState: "released_free", charged: null, resultAvailable: false }) });
    assert.equal(m.kind, "ready");
    if (m.kind !== "ready") continue;
    assert.equal(m.value.phase, "finished");
    assert.ok(m.value.failure !== null, cause);
    const copy = `${m.value.failure.title} ${m.value.failure.action}`;
    assert.ok(m.value.failure.action.length > 20, `${cause}: no action offered`);
    // Sanitized: no internal identifiers, stack words or infrastructure names.
    assert.doesNotMatch(copy, /_|lease|journal|worker|vllm|postgres|stack|exception/i, cause);
  }
  const completed = requestDetailModel({ ok: true, value: job() });
  assert.equal(completed.kind === "ready" && completed.value.failure, null);
  const unknownCause = requestDetailModel({ ok: true, value: job({ state: "failed", outcomeCause: "novel_cause", settlementState: "released_free", resultAvailable: false }) });
  assert.ok(unknownCause.kind === "ready" && unknownCause.value.failure !== null, "an unknown cause still gets generic copy");
});

test(T.expiry, () => {
  const expired = requestDetailModel({ ok: true, value: job({ resultAvailable: false }) });
  assert.equal(expired.kind, "ready");
  if (expired.kind !== "ready") return;
  assert.equal(expired.value.result.access, "expired");
  assert.match(expired.value.result.note, /expired at 2026-09-20 12:10 UTC/);
  // Metadata and charge stay.
  assert.equal(expired.value.charge.amount, "1.23456789 credits");
  assert.equal(expired.value.requestId, ID);

  const available = requestDetailModel({ ok: true, value: job() });
  assert.equal(available.kind === "ready" && available.value.result.access, "available");
  assert.equal(available.kind === "ready" && available.value.result.expiresAt, "2026-09-20T12:10:00.000000Z");
  assert.match(available.kind === "ready" ? available.value.result.note : "", /until 2026-09-20 12:10 UTC/);
});

test(T.poll, () => {
  const delays = Array.from({ length: MAX_POLLS }, (_, n) => pollDelayMs(n) as number);
  assert.equal(delays[0], 2000);
  for (let n = 1; n < delays.length; n += 1) assert.ok(delays[n] >= delays[n - 1], "backoff never shortens");
  assert.equal(Math.max(...delays), 30_000, "capped");
  assert.equal(pollDelayMs(MAX_POLLS), null, "bounded: the poller stops");
  assert.equal(pollDelayMs(-1), null);
  const total = delays.reduce((a, b) => a + b, 0);
  assert.ok(total <= 15 * 60_000, `polls for ${total} ms`);

  const poll = (value: Parameters<typeof requestDetailModel>[0]) => pollsFor(requestDetailModel(value));
  assert.equal(poll({ ok: true, value: job({ state: "queued", outcomeCause: null, settlementState: null, resultAvailable: false }) }), true);
  assert.equal(poll({ ok: true, value: job({ state: "running", outcomeCause: null, settlementState: null, resultAvailable: false }) }), true);
  assert.equal(poll({ ok: true, value: job() }), false, "a finished request is not polled");
  assert.equal(poll({ ok: true, value: fixtureJobs[3] }), false, "held_unknown is finished; reconciliation takes hours");
  assert.equal(poll({ ok: false, error: { code: "dependency_unavailable", message: "x" } }), true, "an outage is retried with backoff");
  assert.equal(poll({ ok: false, error: { code: "forbidden", message: "x" } }), false, "a refusal is not retried");
  assert.equal(poll({ ok: true, value: null }), false, "not found is not polled");
});

test(T.client, () => {
  const ready = { state: "ready", text: "verdict" };
  assert.deepEqual(clientResultRead({ status: 200, redirected: false, json: true }, ready), ready);
  assert.deepEqual(clientResultRead({ status: 200, redirected: true, json: false }, null), { state: "signed_out" }, "middleware sent the fetch to /login");
  assert.deepEqual(clientResultRead({ status: 200, redirected: true, json: true }, ready), { state: "signed_out" }, "a redirect is never trusted");
  assert.deepEqual(clientResultRead({ status: 401, redirected: false, json: true }, { state: "signed_out" }), { state: "signed_out" });
  assert.deepEqual(clientResultRead({ status: 410, redirected: false, json: true }, { state: "expired" }), { state: "expired" });
  assert.deepEqual(clientResultRead({ status: 409, redirected: false, json: true }, { state: "pending" }), { state: "pending" });
  assert.deepEqual(clientResultRead({ status: 502, redirected: false, json: false }, null), { state: "unavailable" });
  assert.deepEqual(clientResultRead({ status: 200, redirected: false, json: true }, { state: "ready" }), { state: "unavailable" }, "ready without text");
  assert.deepEqual(clientResultRead({ status: 200, redirected: false, json: true }, { state: "ready", text: 5 }), { state: "unavailable" });
  assert.deepEqual(clientResultRead({ status: 200, redirected: false, json: true }, { state: "__proto__" }), { state: "unavailable" });
  assert.deepEqual(clientResultRead({ status: 410, redirected: false, json: true }, ready), { state: "unavailable" }, "status and body disagree");
});

test(T.timer, () => {
  const expires = "2026-09-21T11:00:05.000000Z";
  const at = Date.parse("2026-09-21T11:00:05Z");
  assert.equal(expiryDelayMs(expires, at - 1500), 1500);
  assert.equal(expiryDelayMs(expires, at), 0, "at the instant: equality has passed");
  assert.equal(expiryDelayMs(expires, at + 10), 0);
  assert.equal(expiryDelayMs(expires, at - 40 * 86_400_000), 2 ** 31 - 1, "capped to what setTimeout accepts; re-armed on fire");
  assert.equal(expiryDelayMs("not a time", at), 0, "an unreadable expiry drops content now");
});

test(T.retry, () => {
  assert.match(RETRY_GUIDANCE, /new request/i);
  assert.match(RETRY_GUIDANCE, /charged separately/i);
  assert.match(RETRY_GUIDANCE, /Idempotency-Key/);
  assert.match(RETRY_GUIDANCE, /24 hours/);
  // Nothing in the detail route can submit inference: no POST, no inference /v1 call, no server action;
  // its API calls are reads (AP-09: `/console/v1/*` GETs only).
  for (const file of ["page.tsx", "result-panel.tsx", "status-poller.tsx", "result/route.ts", "request-reads.ts", "request-view-model.ts"]) {
    const source = route(file);
    assert.doesNotMatch(source, /method:\s*["']POST|["'`]\/v1\/|\.call\(\s*["'](post|put|patch|delete)["']|use server|chat\/completions|export (async )?function (POST|PUT|PATCH|DELETE)/, file);
  }
  const all = ["page.tsx", "result-panel.tsx", "request-view-model.ts"].map(route).join("\n");
  assert.doesNotMatch(all, /120[- ]second|120 ?s\b|zero[- ]data[- ]retention|\bZDR\b|never stores?/i, "no retention claims");
});

test(T.source, async () => {
  // The preview's reads are the production adapter over the client fake; the gate is apiSource's.
  const preview = apiRequestReads(fakeConsoleApi());
  const demo = await preview.result(ID);
  assert.equal(demo.state, "ready");
  assert.match(demo.state === "ready" ? demo.text : "", /^Demo result/, "fake content says it is a demo");
  assert.deepEqual(await preview.result("b1000000-0000-4000-8000-000000000003"), { state: "pending" });
  assert.deepEqual(await preview.result("b1000000-0000-4000-8000-000000000002"), { state: "unavailable" });
  assert.deepEqual(await preview.job("nope"), { ok: true, value: null });
  // The glue routes through apiSource (the one gate) and names no fake itself; no session is signed out.
  const glue = route("request-context.ts");
  assert.match(glue, /const \{ api, preview \} = await apiSource\(\);\n  if \(!preview && \(await accessToken\(\)\) === null\) return null;/);
  assert.doesNotMatch(glue, /fakeConsole|previewAllowed|defaultWorld/);
});

test(T.surface, () => {
  const panel = route("result-panel.tsx");
  assert.match(panel, /^"use client";/);
  assert.doesNotMatch(panel, /localStorage|sessionStorage|indexedDB|console\.|caches\.|navigator\.serviceWorker/);
  const page = route("page.tsx");
  // The page hands the client only the id and the persisted expiry, never content.
  assert.doesNotMatch(page, /consumer_job_result|\.result\(/);
  assert.doesNotMatch(page, /console\./);
  const handler = route("result/route.ts");
  assert.match(handler, /export const dynamic = "force-dynamic"/);
  assert.match(handler, /resultResponse\(/);
  assert.doesNotMatch(handler, /console\./);
});

test(T.loop, () => {
  const timers = fakeTimers();
  let refreshes = 0;
  let stops = 0;
  pollLoop(timers.schedule, () => (refreshes += 1), () => (stops += 1));
  const delays: number[] = [];
  // Bounded, so a loop that never stops fails here instead of hanging the suite.
  for (let n = 0; n < MAX_POLLS + 5 && timers.live().length > 0; n += 1) {
    delays.push(timers.live()[0].ms);
    timers.fire();
  }
  assert.equal(refreshes, MAX_POLLS);
  assert.equal(stops, 1, "the poller says it stopped");
  assert.equal(timers.live().length, 0, "nothing is armed after the last poll");
  assert.deepEqual(delays, Array.from({ length: MAX_POLLS }, (_, n) => pollDelayMs(n)), "the backoff, in order");

  const unmounted = fakeTimers();
  let after = 0;
  const cancel = pollLoop(unmounted.schedule, () => (after += 1), () => assert.fail("stopped on unmount"));
  unmounted.fire();
  unmounted.fire();
  cancel();
  assert.equal(unmounted.live().length, 0, "navigation away cancels the next poll");
  assert.equal(after, 2);
});

test(T.watch, () => {
  const at = Date.parse("2026-09-21T11:00:05Z");
  const expires = "2026-09-21T11:00:05.000000Z";

  const open = fakeTimers(at - 1500);
  let dropped = 0;
  watchExpiry(expires, open.now, open.schedule, () => (dropped += 1));
  assert.equal(open.live()[0].ms, 1500, "armed for the persisted expiry");
  assert.equal(dropped, 0, "not before it is due");
  open.fire();
  assert.equal(dropped, 1, "dropped at the instant");
  assert.equal(open.live().length, 0);

  // A timer that fires before the clock reaches the expiry (the setTimeout ceiling, a slept
  // laptop) re-arms for what is left instead of dropping early.
  const long = fakeTimers(at - 40 * 86_400_000);
  let longDropped = 0;
  watchExpiry(expires, long.now, long.schedule, () => (longDropped += 1));
  assert.equal(long.live()[0].ms, 2 ** 31 - 1);
  for (let n = 0; n < 5 && long.live().length > 0; n += 1) {
    long.fire();
    if (long.now() < at) assert.equal(longDropped, 0, "dropped before the expiry");
  }
  assert.equal(longDropped, 1, "dropped once, at the expiry");
  assert.equal(long.now(), at);

  const early = fakeTimers(at - 1000);
  let earlyDropped = 0;
  watchExpiry(expires, early.now, early.schedule, () => (earlyDropped += 1));
  const [timer] = early.live();
  timer.live = false;
  early.set(at - 500);
  timer.run(); // the timer fired, but the clock says 500 ms remain
  assert.equal(earlyDropped, 0, "never before it is due");
  assert.equal(early.live()[0].ms, 500, "re-armed for the rest");

  const due = fakeTimers(at + 10);
  let dueDropped = 0;
  watchExpiry(expires, due.now, due.schedule, () => (dueDropped += 1));
  assert.equal(dueDropped, 1, "content that arrives already expired is dropped at once");

  const gone = fakeTimers(at - 1500);
  const cancel = watchExpiry(expires, gone.now, gone.schedule, () => assert.fail("dropped after unmount"));
  cancel();
  assert.equal(gone.live().length, 0, "unmount clears the timer");
});

test(T.reads, async () => {
  const page = new EventTarget();
  const signals: AbortSignal[] = [];
  const answers: (ResultRead | null)[] = [{ state: "ready", text: "the verdict" }, { state: "expired" }];
  const read = async (signal: AbortSignal) => {
    signals.push(signal);
    return answers.shift() ?? null;
  };
  const shown: Shown[] = [];
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const restore = (persisted: boolean) => page.dispatchEvent(Object.assign(new Event("pageshow"), { persisted }));

  const cancel = watchResult(read, (s) => shown.push(s), page);
  await settle();
  assert.deepEqual(shown, [{ state: "ready", text: "the verdict" }], "read on mount");

  restore(false); // the first load's own pageshow is not a restore
  await settle();
  assert.equal(signals.length, 1);

  restore(true); // back/forward cache: hide the old content, then read again
  assert.deepEqual(shown.at(-1), { state: "loading" }, "the old content is not shown from memory");
  await settle();
  assert.equal(signals.length, 2, "re-read on a back/forward restore");
  assert.deepEqual(shown.at(-1), { state: "expired" });

  cancel();
  assert.ok(signals.every((s) => s.aborted), "navigation away aborts the reads");
  const count = shown.length;
  restore(true);
  await settle();
  assert.equal(signals.length, 2, "no read after unmount");
  assert.equal(shown.length, count);

  // A read that answers after navigation shows nothing.
  let answer: (value: ResultRead) => void = () => {};
  const late: Shown[] = [];
  const stop = watchResult(() => new Promise((resolve) => (answer = resolve)), (s) => late.push(s), new EventTarget());
  stop();
  answer({ state: "ready", text: "late" });
  await settle();
  assert.deepEqual(late, [], "an answer after navigation is dropped");
});

test(T.fetch, async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const fetcher = (answer: () => Promise<Response>) =>
    (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return answer();
    }) as unknown as typeof fetch;

  const controller = new AbortController();
  const ready = await readResult("b1/../x", controller.signal, fetcher(async () => json(200, { state: "ready", text: "v" })));
  assert.deepEqual(ready, { state: "ready", text: "v" });
  assert.equal(calls[0].url, "/usage/b1%2F..%2Fx/result", "the id is one path segment");
  assert.equal(calls[0].init.cache, "no-store", "the browser never caches the result");
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.equal(calls[0].init.signal, controller.signal, "navigation can abort it");
  assert.equal(calls[0].init.method ?? "GET", "GET");

  assert.deepEqual(await readResult(ID, undefined, fetcher(async () => json(410, { state: "expired" }))), { state: "expired" });
  assert.deepEqual(await readResult(ID, undefined, fetcher(async () => new Response("<html>", { status: 502 }))), { state: "unavailable" });
  assert.deepEqual(await readResult(ID, undefined, fetcher(() => Promise.reject(new TypeError("fetch failed")))), { state: "unavailable" });

  const gone = new AbortController();
  gone.abort();
  const abandoned = await readResult(ID, gone.signal, fetcher(() => Promise.reject(new DOMException("aborted", "AbortError"))));
  assert.equal(abandoned, null, "an abandoned read shows nothing");
});

test(T.wiring, () => {
  const panel = code("result-panel.tsx");
  assert.doesNotMatch(panel, /\bfetch\(|XMLHttpRequest|EventSource/, "every result read goes through the tested readResult");
  assert.match(panel, /useEffect\(\(\) => watchResult\(\(signal\) => readResult\(requestId, signal\), setShown, window\), \[requestId\]\)/);
  assert.match(panel, /if \(shown\.state !== "ready"\) return;\s+return watchExpiry\(expiresAt, Date\.now, browserTimer, \(\) => setShown\(\{ state: "expired" \}\)\);/);
  const poller = code("status-poller.tsx");
  assert.match(poller, /useEffect\(\(\) => pollLoop\(browserTimer, \(\) => router\.refresh\(\), \(\) => setStopped\(true\)\), \[router, round\]\)/);
  assert.doesNotMatch(poller, /setTimeout|setInterval/, "the poller's timing is the tested loop");
  const page = code("page.tsx");
  assert.equal(page.match(/<StatusPoller\b/g)?.length, 1);
  assert.match(page, /\{pollsFor\(model\) \? <StatusPoller \/> : null\}/, "mounted only when the model polls");
});
