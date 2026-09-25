// node --test "tests/**/*.test.ts"
//
// U4: the owned request detail and its result lifecycle (USER-RESULTS, RESULT-EXPIRY,
// CONSOLE-TENANT). The reads (`usage/[requestId]/request-reads.ts`) run against a recording double
// of the supabase-js calls they make; the view model (`request-view-model.ts`) is pure. The same
// reads run against real PostgreSQL as the browser principal in `request-pg.test.ts`.
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

import type { Answer, ConsumerJob, CreditClient } from "../../app/(console)/billing/credit-reads.ts";
import { defaultCreditFixture } from "../../app/(console)/billing/credit-fixture.ts";
import {
  fixtureRequestReads,
  postgrestRequestReads,
  requestSource,
  resultResponse,
  type RequestSource,
  type ResultRead,
} from "../../app/(console)/usage/[requestId]/request-reads.ts";
import {
  MAX_POLLS,
  RETRY_GUIDANCE,
  clientResultRead,
  expiryDelayMs,
  pollDelayMs,
  requestDetailModel,
  resultAccessOf,
} from "../../app/(console)/usage/[requestId]/request-view-model.ts";

const app = join(dirname(fileURLToPath(import.meta.url)), "../..");
const route = (file: string) => readFileSync(join(app, "app/(console)/usage/[requestId]", file), "utf8");

const USER = "c1000000-0000-4000-8000-000000000001";
const ID = "b1000000-0000-4000-8000-000000000001";

/** A `consumer_jobs` row as PostgREST renders it (0021): money as text, instants `+00:00`. */
function row(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    request_id: ID,
    job_handle: "job_1",
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
    unit: "CREDIT",
    hold: "5.00000000",
    hold_state: "settled",
    charged: "1.23456789",
    result_available: true,
    result_expires_at: "2026-09-21T11:00:05+00:00",
    settled_at: "2026-09-20T11:00:05+00:00",
    cursor: `2026-09-20 11:00:00+00|${ID}`,
    ...over,
  };
}

/** Records every rpc; answers from `script` by function name, in order. */
function recording(script: Record<string, (Answer | Error)[]>) {
  const calls: { fn: string; args: Record<string, unknown> }[] = [];
  const client = {
    from() {
      throw new Error("the request reads use only the consumer RPCs");
    },
    rpc(fn: string, args: Record<string, unknown>) {
      calls.push({ fn, args });
      const answer = script[fn]?.shift();
      if (answer === undefined) return Promise.reject(new Error(`unscripted call to ${fn}`));
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    },
  } as unknown as CreditClient;
  return { client, calls };
}

const ok = (data: unknown): Answer => ({ data, error: null });
const err = (code: string, message: string): Answer => ({ data: null, error: { code, message } });

function job(over: Partial<ConsumerJob> = {}): ConsumerJob {
  const base = defaultCreditFixture().jobs[0];
  return { ...base, ...over };
}

const T = {
  job: "U4-R01 the job read asks consumer_jobs for exactly this request, as the session's own user",
  foreign: "U4-R02 a foreign, unknown, mismatched or malformed id is 'not found', and a malformed one never reaches the database",
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
  retry: "U4-V08 no control submits another inference; retry guidance says a rerun is a new, separately charged request",
  source: "U4-G01 the request fixture is reachable only through the development preview gate",
  surface: "U4-S01 content stays out of the page payload, browser storage and logs; the route answers no-store",
};

test(T.job, async () => {
  const { client, calls } = recording({ consumer_jobs: [ok([row()])] });
  const read = await postgrestRequestReads(client, USER).job(ID.toUpperCase());
  assert.equal(read.ok, true);
  assert.equal(read.ok && read.value?.requestId, ID);
  assert.equal(read.ok && read.value?.charged, "1.23456789");
  assert.deepEqual(calls, [{ fn: "consumer_jobs", args: { p_after: null, p_limit: 2, p_request_id: ID } }]);
});

test(T.foreign, async () => {
  // Another tenant's id and an unknown one answer the same: consumer_jobs returns no row.
  const empty = recording({ consumer_jobs: [ok([])] });
  assert.deepEqual(await postgrestRequestReads(empty.client, USER).job(ID), { ok: true, value: null });

  // A row for a different request is never shown as this one.
  const other = "b1000000-0000-4000-8000-000000000002";
  const mismatched = recording({ consumer_jobs: [ok([row({ request_id: other })])] });
  assert.deepEqual(await postgrestRequestReads(mismatched.client, USER).job(ID), { ok: true, value: null });

  for (const bad of ["", "abc", `${ID}x`, `${ID}' or '1'='1`, "../billing", ID.replaceAll("-", "")]) {
    const none = recording({});
    const reads = postgrestRequestReads(none.client, USER);
    assert.deepEqual(await reads.job(bad), { ok: true, value: null }, bad);
    assert.deepEqual(await reads.result(bad), { state: "not_found" }, bad);
    assert.equal(none.calls.length, 0, `${bad} reached the database`);
  }
});

test(T.jobErrors, async () => {
  const cases: [Answer | Error, string][] = [
    [new Error("socket hang up"), "dependency_unavailable"],
    [err("PGRST301", "JWT expired"), "dependency_unavailable"],
    [err("42501", "not signed in"), "forbidden"],
    [ok([row({ charged: 1.23456789 })]), "internal_error"], // money through a double is refused
    [ok([row({ unit: "USD" })]), "internal_error"], // a CREDIT job relabelled USD
  ];
  for (const [answer, code] of cases) {
    const { client } = recording({ consumer_jobs: [answer] });
    const read = await postgrestRequestReads(client, USER).job(ID);
    assert.equal(read.ok, false);
    assert.equal(!read.ok && read.error.code, code);
  }
});

test(T.gate, async () => {
  // Available: the job read, then the content read.
  const ready = recording({ consumer_jobs: [ok([row()])], consumer_job_result: [ok("a sop verdict")] });
  assert.deepEqual(await postgrestRequestReads(ready.client, USER).result(ID), { state: "ready", text: "a sop verdict" });
  assert.deepEqual(ready.calls.map((c) => c.fn), ["consumer_jobs", "consumer_job_result"]);
  assert.deepEqual(ready.calls[1].args, { p_request_id: ID });

  // The API withholds these (F2C.b read_outcome), so the content read is never made.
  const withheld: [Record<string, unknown>, ResultRead][] = [
    [{ state: "running", outcome_cause: null, settlement_state: null, usage_certainty: null, prompt_tokens: null, completion_tokens: null, hold_state: "held", charged: null, result_available: false, result_expires_at: null }, { state: "pending" }],
    [{ settlement_state: "held_unknown", usage_certainty: "unknown", prompt_tokens: null, completion_tokens: null, hold_state: "unknown", charged: null, result_available: false }, { state: "withheld" }],
    [{ result_available: false }, { state: "expired" }],
    [{ result_available: false, result_expires_at: null }, { state: "unavailable" }],
    [{ state: "failed", outcome_cause: "engine_error", settlement_state: "released_platform_absorbed", charged: null, result_available: false, result_expires_at: null }, { state: "no_result" }],
  ];
  for (const [over, expected] of withheld) {
    const { client, calls } = recording({ consumer_jobs: [ok([row(over)])] });
    assert.deepEqual(await postgrestRequestReads(client, USER).result(ID), expected, JSON.stringify(over));
    assert.deepEqual(calls.map((c) => c.fn), ["consumer_jobs"], `content was read for ${expected.state}`);
  }

  const foreign = recording({ consumer_jobs: [ok([])] });
  assert.deepEqual(await postgrestRequestReads(foreign.client, USER).result(ID), { state: "not_found" });
  assert.equal(foreign.calls.length, 1);

  const down = recording({ consumer_jobs: [new Error("ECONNRESET")] });
  assert.deepEqual(await postgrestRequestReads(down.client, USER).result(ID), { state: "unavailable" });

  // consumer_jobs' only 42501 is "not signed in" (or anon, which holds no EXECUTE): the session ended.
  const signedOut = recording({ consumer_jobs: [err("42501", "not signed in")] });
  assert.deepEqual(await postgrestRequestReads(signedOut.client, USER).result(ID), { state: "signed_out" });
});

test(T.result, async () => {
  // Between the two reads the job may expire, or the session may end: the content read decides.
  const cases: [Answer | Error, ResultRead][] = [
    [err("P0001", "not_found: no result for request x"), { state: "not_found" }],
    [err("P0001", "result_pending: the job has no committed outcome yet"), { state: "pending" }],
    [err("P0001", "result_expired: the result expired at 2026-09-21 11:00:05+00"), { state: "expired" }],
    [err("42501", "not signed in"), { state: "signed_out" }],
    [err("PGRST301", "JWT expired"), { state: "signed_out" }],
    [err("PGRST303", "JWT claims check failed"), { state: "signed_out" }],
    [err("P0001", "something_else: detail"), { state: "unavailable" }],
    [err("57014", "canceling statement due to statement timeout"), { state: "unavailable" }],
    [new Error("fetch failed"), { state: "unavailable" }],
    [ok(null), { state: "unavailable" }],
    [ok({ text: "x" }), { state: "unavailable" }],
    [ok(""), { state: "ready", text: "" }],
  ];
  for (const [answer, expected] of cases) {
    const { client } = recording({ consumer_jobs: [ok([row()])], consumer_job_result: [answer] });
    assert.deepEqual(await postgrestRequestReads(client, USER).result(ID), expected, JSON.stringify(answer));
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
  const jobs = defaultCreditFixture().jobs;
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

  const unknown = requestDetailModel({ ok: true, value: defaultCreditFixture().jobs[3] });
  assert.equal(unknown.kind === "ready" && unknown.value.charge.amount, null, "unknown usage is never a zero charge");
  assert.equal(unknown.kind === "ready" && unknown.value.charge.label, "Awaiting reconciliation");

  const legacy = requestDetailModel({ ok: true, value: defaultCreditFixture().jobs[7] });
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

  const poll = (value: Parameters<typeof requestDetailModel>[0]) => {
    const m = requestDetailModel(value);
    return m.kind === "ready" ? m.value.poll : m.kind === "error" ? m.poll : false;
  };
  assert.equal(poll({ ok: true, value: job({ state: "queued", outcomeCause: null, settlementState: null, resultAvailable: false }) }), true);
  assert.equal(poll({ ok: true, value: job({ state: "running", outcomeCause: null, settlementState: null, resultAvailable: false }) }), true);
  assert.equal(poll({ ok: true, value: job() }), false, "a finished request is not polled");
  assert.equal(poll({ ok: true, value: defaultCreditFixture().jobs[3] }), false, "held_unknown is finished; reconciliation takes hours");
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
  // Nothing in the detail route can submit inference: no POST, no /v1 call, no server action.
  for (const file of ["page.tsx", "result-panel.tsx", "status-poller.tsx", "result/route.ts", "request-reads.ts", "request-view-model.ts"]) {
    const source = route(file);
    assert.doesNotMatch(source, /method:\s*["']POST|\/v1\/|use server|chat\/completions|export (async )?function (POST|PUT|PATCH|DELETE)/, file);
  }
  const all = ["page.tsx", "result-panel.tsx", "request-view-model.ts"].map(route).join("\n");
  assert.doesNotMatch(all, /120[- ]second|120 ?s\b|zero[- ]data[- ]retention|\bZDR\b|never stores?/i, "no retention claims");
});

test(T.source, async () => {
  let calls = 0;
  const real: RequestSource = { reads: fixtureRequestReads(), preview: false };
  const pick = (env: Record<string, string>) => requestSource(async () => ((calls += 1), real), env);
  assert.equal((await pick({ NODE_ENV: "production", INFRX_CONSOLE_PREVIEW: "1" }))?.preview, false, "production opt-in");
  assert.ok((await pick({ NODE_ENV: "development" })) === real, "development without opt-in");
  assert.equal(calls, 2);
  const preview = (await pick({ NODE_ENV: "development", INFRX_CONSOLE_PREVIEW: "1" })) as RequestSource;
  assert.equal(preview.preview, true);
  assert.equal(calls, 2, "the real session is not opened for the preview");
  // The fixture's reads follow the same rules as the real ones.
  const demo = await preview.reads.result(ID);
  assert.equal(demo.state, "ready");
  assert.match(demo.state === "ready" ? demo.text : "", /^Demo result/, "fixture content says it is a demo");
  assert.deepEqual(await preview.reads.result("b1000000-0000-4000-8000-000000000003"), { state: "pending" });
  assert.deepEqual(await preview.reads.result("b1000000-0000-4000-8000-000000000002"), { state: "unavailable" });
  assert.deepEqual(await preview.reads.job("nope"), { ok: true, value: null });
  // The glue routes through the gate and names no fixture itself.
  const glue = route("request-context.ts");
  assert.match(glue, /return requestSource\(async \(\) => \{/);
  assert.doesNotMatch(glue, /fixtureRequestReads|previewAllowed|defaultCreditFixture/);
});

test(T.surface, () => {
  const panel = route("result-panel.tsx");
  assert.match(panel, /^"use client";/);
  assert.match(panel, /cache: "no-store"/);
  assert.match(panel, /credentials: "same-origin"/);
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
