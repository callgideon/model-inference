// C3F WR-C3F-2: `consoleActions().submitFeedback` as `app/actions.ts` composes it - the Origin check
// before anyone is resolved, the context and the individual's own client, a refresh only after an
// acknowledgment, and no client an explicit unavailable state.
import assert from "node:assert/strict";
import test from "node:test";
import type { ConsumerContext } from "../../../lib/contracts/v2/consumer.ts";
import { consoleActions, type ActionDeps } from "../../../lib/services/actions.ts";
import type { FeedbackRpc } from "../../../lib/services/feedback.ts";

const JOB = "5c000000-0000-4000-8000-0000000000f1";
const ME = "c1000000-0000-4000-8000-000000000001";
const ready: ConsumerContext = { state: "ready", account: { userId: ME, email: "me@example.com", walletId: "aaaaaaaa-0000-4000-8000-000000000001", orgId: "0e000000-0000-4000-8000-000000000001", suspended: false } };
const ROW = { feedback_id: "fb_" + "a".repeat(64), request_id: JOB, author_principal: ME, author_role: "customer", channel: "console", name: "thumb", value: true, comment: null, calibration_set: false, rubric_version: null, created_at: "2026-09-27T00:00:00+00:00" };
const input = { request_id: JOB, name: "thumb" as const, value: true, idempotency_key: "k" };

function deps(origin: string, answer: unknown, feedback = true) {
  const seen = { resolved: 0, calls: 0, refreshed: [] as string[] };
  const rpc: FeedbackRpc = { rpc: () => { seen.calls += 1; return Promise.resolve({ data: answer, error: null }); } };
  const d = {
    headers: async () => new Headers({ origin, host: "app.example" }),
    context: async () => { seen.resolved += 1; return ready; },
    revalidate: (path: string) => seen.refreshed.push(path),
    ...(feedback ? { feedback: async () => rpc } : {}),
  } as unknown as ActionDeps;
  return { d, seen };
}

test("C3F-C01 the composed action refuses a cross-site request before resolving anyone", async () => {
  const { d, seen } = deps("https://evil.example", ROW);
  const result = await consoleActions(d).submitFeedback(input);
  assert.equal(result.ok ? "ok" : result.error.code, "forbidden");
  assert.deepEqual([seen.resolved, seen.calls], [0, 0]);
});

test("C3F-C02 an acknowledged signal refreshes the traces; a refusal or no client refreshes nothing", async () => {
  const good = deps("https://app.example", ROW);
  assert.equal((await consoleActions(good.d).submitFeedback(input)).ok, true, "acknowledged");
  assert.deepEqual(good.seen.refreshed, ["/traces"]);
  const bad = deps("https://app.example", { ...ROW, author_role: "operator" });
  assert.equal((await consoleActions(bad.d).submitFeedback(input)).ok, false, "not a customer signal");
  assert.deepEqual(bad.seen.refreshed, []);
  const none = deps("https://app.example", ROW, false);
  const off = await consoleActions(none.d).submitFeedback(input);
  assert.equal(off.ok ? "ok" : off.error.code, "dependency_unavailable");
});
