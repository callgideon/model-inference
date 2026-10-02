// C3F: `consoleActions().submitFeedback` as `app/actions.ts` composes it - the Origin check before
// anything runs and the request's API client; nothing is refreshed (no App page lists feedback).
import assert from "node:assert/strict";
import test from "node:test";
import { answer, recordingApi } from "../../../lib/fake-api.ts";
import { consoleActions, type ActionDeps } from "../../../lib/services/actions.ts";

const JOB = "5c000000-0000-4000-8000-0000000000f1";
const ACK = { feedback_id: "fb_1", request_id: JOB, author_role: "customer", channel: "console", created_at: "2026-09-27T00:00:00Z" };
const input = { request_id: JOB, name: "thumb" as const, value: true, idempotency_key: "k" };

function deps(origin: string) {
  const seen = { resolved: 0, refreshed: [] as string[] };
  const { api, sent } = recordingApi(() => answer(201, ACK));
  const d = {
    headers: async () => new Headers({ origin, host: "app.example" }),
    api: async () => {
      seen.resolved += 1;
      return api;
    },
    revalidate: (path: string) => seen.refreshed.push(path),
  } as unknown as ActionDeps;
  return { d, seen, sent };
}

test("C3F-C01 the composed action refuses a cross-site request before resolving anyone", async () => {
  const { d, seen, sent } = deps("https://evil.example");
  const result = await consoleActions(d).submitFeedback(input);
  assert.equal(result.ok ? "ok" : result.error.code, "forbidden");
  assert.deepEqual([seen.resolved, sent.length], [0, 0]);
});

test("C3F-C02 a same-site signal reaches the API once as the session and refreshes nothing", async () => {
  const { d, seen, sent } = deps("https://app.example");
  assert.equal((await consoleActions(d).submitFeedback(input)).ok, true, "acknowledged");
  assert.deepEqual([seen.resolved, sent.length, seen.refreshed], [1, 1, []]);
});
