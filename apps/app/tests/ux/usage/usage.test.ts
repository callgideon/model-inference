// node --test "tests/**/*.test.ts"
//
// UX-07 (04-consumer.md C-04/C-05/C-07, UX-T05): the consumer's usage, request detail, credits and
// settings presentation over the existing view models (no new read, no new action). Failure oracles: a
// result note or privacy row that claims physical deletion (WR-UXF-4/5), a detail that hides a state
// or shows content for a request the store would not serve, a retry that submits inference, a wallet
// failure or an unreadable charge rendered as a zero, a tiny debit read as free, execution and money
// states merged into one label, and a hold called spent.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { defaultCreditFixture } from "../../../app/(console)/billing/credit-fixture.ts";
import type { ConsumerJob } from "../../../app/(console)/billing/credit-reads.ts";
import {
  copyText,
  pollsFor,
  readResult,
  requestDetailModel,
  watchExpiry,
  watchResult,
  type Schedule,
  type Shown,
} from "../../../app/(console)/usage/[requestId]/request-view-model.ts";
import { settingsModel } from "../../../app/(console)/settings/view-model.ts";

import { render, text } from "./render.ts";

const APP = new URL("../../../", import.meta.url).pathname;
const source = (path: string) => readFileSync(APP + path, "utf8");
const job = (over: Partial<ConsumerJob> = {}): ConsumerJob => ({ ...defaultCreditFixture().jobs[0], ...over });
const DELETION = /\b(remov|delet|purg|eras|destroy)/i;

test("UXU-01 result notes say when the result stops being readable, never that content is removed or deleted", () => {
  const available = requestDetailModel({ ok: true, value: job({ resultAvailable: true }) });
  const expired = requestDetailModel({ ok: true, value: job({ resultAvailable: false }) });
  assert.ok(available.kind === "ready" && expired.kind === "ready");
  assert.equal(available.value.result.access, "available");
  assert.equal(
    available.value.result.note,
    "Readable until 2026-09-20 12:10 UTC. After that the result is no longer available; this page keeps the request's details and charge.",
  );
  assert.equal(expired.value.result.access, "expired");
  assert.equal(
    expired.value.result.note,
    "The result stopped being available at 2026-09-20 12:10 UTC. Request status and usage remain available.",
  );
  for (const over of [{}, { resultAvailable: false }, { state: "running" }, { state: "failed" }, { resultExpiresAt: null }, { settlementState: "held_unknown" }]) {
    const model = requestDetailModel({ ok: true, value: job(over) });
    assert.ok(model.kind === "ready");
    assert.doesNotMatch(model.value.result.note, DELETION, JSON.stringify(over));
  }
});

test("UXU-02 Settings' serving retention says stored content stops being readable, with no deletion promise", () => {
  const retention = settingsModel({ state: "ready", account: { email: "a@example.com", suspended: false } } as never).privacy.find((r) =>
    /retention/i.test(r.title),
  );
  assert.ok(retention);
  assert.match(retention.detail, /for limited periods, after which it can no longer be read/);
  for (const row of settingsModel({ state: "unavailable" } as never).privacy) {
    assert.doesNotMatch(row.detail, /then delete|is deleted|deletes it/i, row.title);
  }
});

// ---------------------------------------------------------------------------------------------------
// Request detail (C-04 detail hierarchy; UX-T05)
// ---------------------------------------------------------------------------------------------------

const DETAIL = "app/(console)/usage/[requestId]/request-detail.tsx";
const ID = "b1000000-0000-4000-8000-000000000001";
const HERE = `/usage/${ID}`;
const CHECKED = "2026-10-02T10:31:05.123Z";
const renderDetail = async (read: Parameters<typeof requestDetailModel>[0]) =>
  render(DETAIL, "RequestDetailView", { model: requestDetailModel(read), here: HERE, checkedAt: CHECKED });
const before = (page: string, first: string, second: string) => {
  const [a, b] = [page.indexOf(first), page.indexOf(second)];
  assert.ok(a !== -1, `missing: ${first}`);
  assert.ok(b !== -1, `missing: ${second}`);
  assert.ok(a < b, `${first} is not before ${second}`);
};

test("UXU-03 the request detail renders the ready, running, failed, expired, not-found and unavailable fixtures, result before charge", async () => {
  const ready = text(await renderDetail({ ok: true, value: job() }));
  assert.match(ready, new RegExp(ID));
  assert.match(ready, /Copy request ID/);
  before(ready, "Finished", "Result");
  before(ready, "Result", "Usage and charge");
  before(ready, "Usage and charge", "Technical details");
  assert.match(ready, /Readable until 2026-09-20 12:10 UTC/);
  assert.match(ready, /Loading the result…/, "a readable result mounts the result panel");
  assert.match(ready, /Charged 1\.23456789 credits/);
  assert.doesNotMatch(ready, /last checked/i, "a finished request is not polled");

  const running = text(await renderDetail({ ok: true, value: job({ state: "running", settlementState: null, holdState: "held", charged: null, resultAvailable: false, resultExpiresAt: null }) }));
  assert.match(running, /Running/);
  assert.match(running, /The result appears here when the request finishes\./);
  assert.doesNotMatch(running, /Loading the result/);
  assert.match(running, /Charged — Held 5\.00 credits/, "a hold is shown as held, never as a charge");
  assert.match(running, /Status last checked 10:31:05 UTC/);

  const failed = text(await renderDetail({ ok: true, value: job({ state: "failed", outcomeCause: "engine_error", settlementState: "released_platform_absorbed", holdState: "released", charged: null, resultAvailable: false, resultExpiresAt: null }) }));
  assert.match(failed, /The request failed on our side\./);
  assert.match(failed, /This request did not produce a result\./);
  assert.match(failed, /No charge/);
  assert.doesNotMatch(failed, /Loading the result/);

  const expired = text(await renderDetail({ ok: true, value: job({ resultAvailable: false }) }));
  assert.match(expired, /The result stopped being available at 2026-09-20 12:10 UTC\. Request status and usage remain available\./);
  assert.doesNotMatch(expired, /Loading the result/, "an expired result is never fetched");
  assert.match(expired, /Charged 1\.23456789 credits/, "status and usage remain");
  assert.match(expired, new RegExp(ID));

  const missing = text(await renderDetail({ ok: true, value: null }));
  assert.match(missing, /We could not find this request in your account\./);
  assert.doesNotMatch(missing, /Usage and charge|credits/);

  const html = await renderDetail({ ok: false, error: { code: "dependency_unavailable", message: "x" } });
  const unavailable = text(html);
  assert.match(unavailable, /This request could not be loaded/);
  assert.match(html, new RegExp(`<a [^>]*href="${HERE}"[^>]*>Try again</a>`));
  assert.doesNotMatch(unavailable, /Usage and charge|credits|0\.00/, "a failed read shows no figure");
});

/** A fake timer and clock: `fire` runs the earliest armed timer, moving the clock to it. */
function fakeTimers() {
  let clock = Date.parse("2026-09-20T12:00:00Z");
  const armed: { at: number; run: () => void; live: boolean }[] = [];
  const schedule: Schedule = (run, ms) => {
    const timer = { at: clock + ms, run, live: true };
    armed.push(timer);
    return () => void (timer.live = false);
  };
  const fire = () => {
    const [next] = armed.filter((t) => t.live).sort((a, b) => a.at - b.at);
    next.live = false;
    clock = next.at;
    next.run();
  };
  return { schedule, now: () => clock, fire };
}

test("UXU-04 UX-T05: a transient read failure, Retry, success, then expiry and a back/forward restore: only reads, never a new inference", async () => {
  // A running request is polled; polling re-renders the page and submits nothing.
  const running = requestDetailModel({ ok: true, value: job({ state: "running", settlementState: null, resultAvailable: false, resultExpiresAt: null }) });
  assert.equal(pollsFor(running), true);

  // The finished request's result: the first read hits an outage, Retry reads again and succeeds.
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const answers = [
    { status: 503, body: { state: "unavailable" } },
    { status: 200, body: { state: "ready", text: "a person waves" } },
    { status: 410, body: { state: "expired" } },
  ];
  const fetcher = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const answer = answers.shift()!;
    return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
  assert.deepEqual(await readResult(ID, undefined, fetcher), { state: "unavailable" });
  const panel = source("app/(console)/usage/[requestId]/result-panel.tsx");
  const retry = panel.slice(panel.indexOf('shown.state === "unavailable" ?'));
  assert.match(retry, /Retry loading result/, "the unavailable state offers a retry");
  const handler = retry.slice(retry.indexOf("onClick="), retry.indexOf("Retry loading result"));
  assert.deepEqual(handler.match(/\b[A-Za-z]+\(/g), ["setShown(", "readResult(", "then(", "setShown("], "Retry only re-reads the result");
  assert.deepEqual(await readResult(ID, undefined, fetcher), { state: "ready", text: "a person waves" });

  // The open page drops the content at the persisted expiry; the panel then says what remains.
  const timers = fakeTimers();
  let shown: Shown = { state: "ready", text: "a person waves" };
  watchExpiry("2026-09-20T12:10:00.000000Z", timers.now, timers.schedule, () => (shown = { state: "expired" }));
  timers.fire();
  assert.equal(shown.state, "expired");
  assert.match(panel, /expired: "This result is no longer available\. Request status and usage remain available\."/);

  // A back/forward restore hides what was shown and reads again: the store says expired.
  const page = new EventTarget();
  const seen: string[] = [];
  const stop = watchResult((signal) => readResult(ID, signal, fetcher), (next) => seen.push(next.state), page);
  await new Promise((done) => setTimeout(done, 0));
  const restore = Object.assign(new Event("pageshow"), { persisted: true });
  answers.unshift({ status: 410, body: { state: "expired" } });
  page.dispatchEvent(restore);
  await new Promise((done) => setTimeout(done, 0));
  stop();
  assert.deepEqual(seen, ["expired", "loading", "expired"]);

  // Every call was a same-origin, no-store GET of this request's result: nothing ran the request again.
  assert.equal(calls.length, 4);
  for (const call of calls) {
    assert.equal(call.url, HERE + "/result");
    assert.equal(call.init?.method, undefined);
    assert.equal(call.init?.body, undefined);
    assert.equal(call.init?.cache, "no-store");
  }
});

test("UXU-05 Copy request ID and Copy result report a blocked clipboard instead of claiming success", async () => {
  const writes: string[] = [];
  assert.equal(await copyText("abc", { writeText: async (t: string) => void writes.push(t) }), "copied");
  assert.deepEqual(writes, ["abc"]);
  assert.equal(await copyText("abc", { writeText: () => Promise.reject(new Error("denied")) }), "failed");
  assert.equal(await copyText("abc", undefined), "failed", "no clipboard API (an insecure origin) is a failure");
  const button = source("app/(console)/usage/[requestId]/copy-button.tsx");
  assert.match(button, /failed: "Copying was blocked\. Select the text and copy it by hand\."/);
  const panel = source("app/(console)/usage/[requestId]/result-panel.tsx");
  assert.match(panel, /<CopyButton text=\{text\} label="Copy result" \/>/);
  assert.match(source(DETAIL), /<CopyButton text=\{detail\.requestId\} label="Copy request ID" \/>/);
});
