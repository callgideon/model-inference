// C3F LAB-ACCESS through the REAL path (Lab audience): `reviewFeedback` over supabase-js with the
// provider user's own signed JWT, PostgREST v13.0.4 and the pinned Supabase PostgreSQL with every
// migration and the doors. Run by apps/app/tests/c/feedback/stack.py (after the App's cases), which
// points INFRX_C3F_STACK at its world; without it every case SKIPS.
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createClient } from "@supabase/supabase-js";
import type { Membership, Role } from "../../../lib/auth/access.ts";
import { reviewFeedback, type ReviewRpc } from "../../../lib/services/review/index.ts";

type Stack = {
  url: string;
  jwt_secret: string;
  jobs: Record<"job_1" | "job_2", string>;
  users: Record<"c1" | "dev" | "admin" | "viewer" | "both", string>;
  providers: Record<"nemo" | "other", string>;
  unknown: string;
};
const manifest = process.env.INFRX_C3F_STACK;
const stack: Stack | null = manifest ? (JSON.parse(readFileSync(manifest, "utf8")) as Stack) : null;
const skip = stack === null ? "needs the task-local stack: run apps/app/tests/c/feedback/stack.py" : false;
const S = stack as Stack;
const PLACEHOLDER = "http://c3f-stack.invalid";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = encode({ alg: "HS256", typ: "JWT" });
  const body = encode({ ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  return `${head}.${body}.${createHmac("sha256", S.jwt_secret).update(`${head}.${body}`).digest("base64url")}`;
}

function clientAs(user: string): ReviewRpc {
  const token = jwt({ role: "authenticated", sub: user });
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

/** What L1's guard hands an action: the selected workspace (its role is display only here). */
const at = (provider: string, role: Role = "developer"): Membership => ({ providerId: provider, providerName: "NemoStation", role });

test("C3F-P03 a provider developer reviews the customer signals a current grant shares, labels and identities excluded", { skip }, async () => {
  for (const user of [S.users.dev, S.users.admin]) {
    const got = await reviewFeedback(clientAs(user), at(S.providers.nemo), S.jobs.job_1);
    assert.ok(got.ok, JSON.stringify(got));
    const channels = got.entries.map((e) => e.channel).sort();
    assert.ok(channels.includes("api") && channels.includes("console"), JSON.stringify(got.entries));
    assert.ok(got.entries.every((e) => e.author_role === "customer" && e.request_id === S.jobs.job_1));
  }
});

test("C3F-P04 the viewer, a revoked grant, another provider, a forged workspace and an unknown request share nothing", { skip }, async () => {
  const cases: [string, string, string, string][] = [
    [S.users.viewer, S.providers.nemo, S.jobs.job_1, "forbidden"],                  // role, not grant
    [S.users.dev, S.providers.nemo, S.jobs.job_2, "not_found"],                     // C2 revoked sharing
    [S.users.both, S.providers.other, S.jobs.job_1, "not_found"],                   // OTHER's grant names its own model only
    [S.users.dev, S.providers.other, S.jobs.job_1, "not_found"],                    // a workspace the user is not in
    [S.users.c1, S.providers.nemo, S.jobs.job_1, "not_found"],                      // the consumer who owns the request
    [S.users.dev, S.providers.nemo, S.unknown, "not_found"],
  ];
  for (const [user, provider, job, reason] of cases) {
    assert.deepEqual(await reviewFeedback(clientAs(user), at(provider), job), { ok: false, reason }, `${user} ${provider} ${job}`);
  }
});
