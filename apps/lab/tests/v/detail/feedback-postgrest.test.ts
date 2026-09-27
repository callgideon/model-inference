// V2 FEEDBACK-ACK through the REAL C3F door: the request page's feedback panel (C3F's reviewFeedback
// -> feedbackView) over supabase-js with the provider user's own JWT, PostgREST and the pinned
// Supabase PostgreSQL (the world of apps/app/tests/c/feedback/stack.py, which points INFRX_C3F_STACK
// at it once WR-V2-3 lists this file; without it every case SKIPS). Outside the fake-driven mutant
// suite, as C3F's own review-postgrest.test.ts is.
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import { reviewFeedback, type ReviewRpc } from "../../../lib/services/review/index.ts";
import { FEEDBACK_COPY, feedbackView } from "../../../components/traces/detail/view.ts";

type Stack = { url: string; jwt_secret: string; jobs: Record<"job_1" | "job_2", string>; users: Record<"dev" | "viewer", string>; providers: Record<"nemo", string> };
const manifest = process.env.INFRX_C3F_STACK;
const S = (manifest ? JSON.parse(readFileSync(manifest, "utf8")) : null) as Stack;
const skip = S === null ? "needs the task-local stack: run apps/app/tests/c/feedback/stack.py (WR-V2-3)" : false;
const PLACEHOLDER = "http://c3f-stack.invalid";

function clientAs(user: string): ReviewRpc {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const body = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ role: "authenticated", sub: user, exp: Math.floor(Date.now() / 1000) + 600 })}`;
  const token = `${body}.${createHmac("sha256", S.jwt_secret).update(body).digest("base64url")}`;
  return createClient(PLACEHOLDER, token, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: {
      fetch: (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return fetch(String(input).replace(`${PLACEHOLDER}/rest/v1`, S.url), { ...init, headers });
      },
    },
  }) as unknown as ReviewRpc;
}
const at = (role: "developer" | "viewer") => ({ providerId: S.providers.nemo, providerName: "NemoStation", role });

test("V2-R01 the panel shows the durable customer signals with their provenance, and never the operator's label", { skip }, async () => {
  const view = feedbackView(await reviewFeedback(clientAs(S.users.dev), at("developer"), S.jobs.job_1));
  assert.equal(view.empty, null, JSON.stringify(view));
  assert.deepEqual([...new Set(view.rows.map((r) => r.who))].sort(), ["customer · api", "customer · console"]);
  assert.doesNotMatch(JSON.stringify(view.rows), /calibration|incorrect|operator/);
});

test("V2-R02 revoked sharing and a viewer get the fixed copy, not an empty review", { skip }, async () => {
  assert.equal(feedbackView(await reviewFeedback(clientAs(S.users.dev), at("developer"), S.jobs.job_2)).empty, FEEDBACK_COPY.not_found);
  assert.equal(feedbackView(await reviewFeedback(clientAs(S.users.viewer), at("viewer"), S.jobs.job_1)).empty, FEEDBACK_COPY.forbidden);
});
