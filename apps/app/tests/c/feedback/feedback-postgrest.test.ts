// node --test "tests/**/*.test.ts"   (skips visibly without the stack)
//
// C3F FEEDBACK-ACK through the REAL path (App audience): `submitOwnFeedback` over supabase-js with
// the individual's own signed JWT, PostgREST v13.0.4 and the pinned Supabase PostgreSQL with every
// migration and the doors. Run by stack.py, which seeds the world and points INFRX_C3F_STACK at it:
//
//     cd apps/infrx-api && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase \
//         uv run --frozen python ../app/tests/c/feedback/stack.py

import assert from "node:assert/strict";
import { createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import type { ConsumerContext } from "../../../lib/contracts/v2/consumer.ts";
import { submitOwnFeedback, type FeedbackRpc } from "../../../lib/services/feedback.ts";

type Stack = { url: string; jwt_secret: string; jobs: Record<"job_1" | "job_2", string>; orgs: Record<"c1" | "c2", string>; users: Record<"c1", string> };
const manifest = process.env.INFRX_C3F_STACK;
const stack: Stack | null = manifest ? (JSON.parse(readFileSync(manifest, "utf8")) as Stack) : null;
const skip = stack === null ? "needs the task-local stack: run tests/c/feedback/stack.py" : false;
const S = stack as Stack;
const PLACEHOLDER = "http://c3f-stack.invalid";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = encode({ alg: "HS256", typ: "JWT" });
  const body = encode({ ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  return `${head}.${body}.${createHmac("sha256", S.jwt_secret).update(`${head}.${body}`).digest("base64url")}`;
}

function clientAs(who: string): FeedbackRpc {
  const token = who === "anon" ? jwt({ role: "anon" }) : jwt({ role: "authenticated", sub: who });
  return createClient(PLACEHOLDER, token, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: {
      fetch: (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return fetch(String(input).replace(`${PLACEHOLDER}/rest/v1`, S.url), { ...init, headers });
      },
    },
  }) as unknown as FeedbackRpc;
}

const ready = (user: string, org: string): ConsumerContext => ({
  state: "ready",
  account: { userId: user, email: "c3f@example.com", walletId: "aaaaaaaa-0000-4000-8000-000000000001", orgId: org, suspended: false },
});

test("C3F-P01 an individual's feedback on their own request is acknowledged once, as their customer console signal", { skip }, async () => {
  const me = ready(S.users.c1, S.orgs.c1);
  const input = { request_id: S.jobs.job_1, name: "correction", value: "the second clip is a dog", idempotency_key: randomUUID() };
  const first = await submitOwnFeedback(me, clientAs(S.users.c1), input);
  assert.ok(first.ok, JSON.stringify(first));
  assert.equal(first.value.author_principal, S.users.c1);
  assert.deepEqual([first.value.author_role, first.value.channel, first.value.calibration_set], ["customer", "console", false]);
  const replay = await submitOwnFeedback(me, clientAs(S.users.c1), input);
  assert.ok(replay.ok && replay.value.id === first.value.id, JSON.stringify(replay));
  const changed = await submitOwnFeedback(me, clientAs(S.users.c1), { ...input, value: "a cat" });
  assert.equal(changed.ok ? "ok" : changed.error.code, "idempotency_conflict");
});

test("C3F-P02 a forged request id from another org is not_found, and anon is refused by the database", { skip }, async () => {
  const me = ready(S.users.c1, S.orgs.c1);
  const forged = await submitOwnFeedback(me, clientAs(S.users.c1), { request_id: S.jobs.job_2, name: "thumb", value: false, idempotency_key: randomUUID() });
  assert.equal(forged.ok ? "ok" : forged.error.code, "not_found");
  // A ready context cannot vouch for a missing session: the database refuses the anonymous role.
  const anon = await submitOwnFeedback(me, clientAs("anon"), { request_id: S.jobs.job_1, name: "thumb", value: true, idempotency_key: randomUUID() });
  assert.equal(anon.ok ? "ok" : anon.error.code, "forbidden");
});
