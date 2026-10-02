// node --test "tests/**/*.test.ts"
//
// A2 over the auth facade (AP-09 09b): public verified onboarding and recovery. The decisions that
// matter (what a callback does, which session a grant is claimed for, what a claim or wallet answer
// MEANS for the page, and what an auth failure is allowed to say) live in `app/(auth)/flow.ts` as pure
// functions over the generated client, so they run here against a recorded `fetch` - no Next, no IdP.
// Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import type { Result as ApiResult } from "@infrx/api-client/transport";
import { answer, envelope as envelopeOf, recordingApi } from "../../lib/fake-api.ts";
import {
  AFTER_VERIFY,
  CAPTCHA_UNAVAILABLE,
  FAILURE_COPY,
  afterSignIn,
  captchaGate,
  claimGrant,
  claimOutcome,
  completeCallback,
  facadeFailure,
  loginNotice,
  onboardingFor,
  requestReset,
  requestResend,
  requestSignup,
  resetRedirect,
  safeNext,
  settled,
  verifyRedirect,
  walletBalance,
  welcomeWallet,
  type CallbackPorts,
  type OnboardingState,
} from "../../app/(auth)/flow.ts";

const USER = "a2a2a2a2-0000-4000-8000-000000000001";
const GRANTED_AT = "2026-09-25T12:00:00+00:00";
const SESSION = { access_token: "eyJ.a.b", refresh_token: "r", token_type: "bearer", expires_in: 3600, expires_at: 1, user_id: USER };
const CLAIM = { status: "granted" as const, credit: { amount: "10000.00000000", unit: "CREDIT" as const }, granted_at: GRANTED_AT };
const CREDITS = {
  wallet_id: "a2a2a2a2-0000-4000-8000-000000000002",
  ledger_total: { amount: "9999.50000000", unit: "CREDIT" as const },
  reserved_total: { amount: "0.00000000", unit: "CREDIT" as const },
  available: { amount: "9999.50000000", unit: "CREDIT" as const },
  spent: { amount: "0.50000000", unit: "CREDIT" as const },
  signup_granted_at: GRANTED_AT,
};

const ok = <T>(data: T): ApiResult<T> => ({ ok: true, status: 200, data, requestId: "r", location: null });
const refused = (code: string, status: number): ApiResult<never> => ({
  ok: false, requestId: "r",
  error: { kind: "error", status, code, message: "SECRET server text", requestId: "r", retryable: false, fieldErrors: [], operationId: null, resourceId: null },
});
const down: ApiResult<never> = { ok: false, requestId: "r", error: { kind: "unavailable", status: null, reason: "network" } };

const recorded = recordingApi;
const json = answer;
const envelope = (code: string) => envelopeOf(code, "SECRET");

type Calls = { landed: string[]; stored: string[]; claims: string[] };

function ports(overrides: Partial<CallbackPorts> = {}, landing: ApiResult<{ session: typeof SESSION; redirect: string }> = ok({ session: SESSION, redirect: AFTER_VERIFY })) {
  const calls: Calls = { landed: [], stored: [], claims: [] };
  const base: CallbackPorts = {
    land: async (params) => {
      calls.landed.push(params.toString());
      return landing;
    },
    store: async (session) => {
      calls.stored.push(session.user_id);
    },
    claim: async (session) => {
      calls.claims.push(session.user_id);
      return { kind: "credited", first: true, amount: "10000.00000000", grantedAt: GRANTED_AT } as OnboardingState;
    },
  };
  return { ports: { ...base, ...overrides }, calls };
}

const q = (text: string) => new URLSearchParams(text);

// ------------------------------------------------------------------------------ callback ---

test("A2-CB-01 a link error is a fixed code; the provider's description is never reflected into the page", async () => {
  const phishing = "Your account is locked. Call +1 555 0100";
  const { ports: expiredPorts, calls } = ports({}, refused("link_expired", 410));
  const expired = await completeCallback(q(`error_code=otp_expired&error_description=${encodeURIComponent(phishing)}`), expiredPorts);
  assert.equal(expired, "/login?error=link_expired", "an expired email link must say so, as a code");
  const { ports: otherPorts } = ports({}, refused("link_invalid", 422));
  const other = await completeCallback(q(`error=server_error&error_description=${encodeURIComponent(phishing)}`), otherPorts);
  assert.equal(other, "/login?error=link_invalid", "any other link failure is the generic invalid-link code");
  for (const target of [expired, other]) assert.ok(!target.includes("locked"), `reflected text in ${target}`);
  assert.deepEqual([calls.stored, calls.claims], [[], []], "a failed link stores and claims nothing");
});

test("A2-CB-02 a callback the facade cannot answer is an invalid link, not a crash or a silent sign-in", async () => {
  for (const landing of [down, refused("unavailable", 503)]) {
    const { ports: p, calls } = ports({}, landing);
    assert.equal(await completeCallback(q("code=c"), p), "/login?error=link_invalid");
    assert.deepEqual([calls.stored, calls.claims], [[], []]);
  }
  const { ports: thrown } = ports({ land: async () => { throw new Error("network"); } });
  assert.equal(await completeCallback(q("code=c"), thrown).catch(() => "threw"), "/login?error=link_invalid");
});

test("A2-CB-03 a verified callback stores the facade's session and claims the grant ONCE, as that session", async () => {
  const { ports: p, calls } = ports();
  const target = await completeCallback(q(`code=pkce-1&user_id=ffffffff-0000-4000-8000-00000000dead&next=/welcome`), p);
  assert.equal(target, AFTER_VERIFY);
  assert.deepEqual(calls.landed, ["code=pkce-1&user_id=ffffffff-0000-4000-8000-00000000dead&next=%2Fwelcome"], "the link goes to the facade as given");
  assert.deepEqual(calls.stored, [USER]);
  assert.deepEqual(calls.claims, [USER], "exactly one claim, as the session the facade issued");
});

test("A2-CB-04 a landing without a usable session is an invalid link and claims nothing", async () => {
  const { ports: p, calls } = ports({}, ok({ session: { ...SESSION, access_token: "" }, redirect: AFTER_VERIFY }));
  assert.equal(await completeCallback(q("token_hash=th-1&type=signup"), p), "/login?error=link_invalid");
  assert.deepEqual(calls.claims, []);
});

test("A2-CB-05 a session that cannot be stored is an invalid link and claims nothing", async () => {
  const { ports: p, calls } = ports({ store: async () => { throw new Error("cookie"); } });
  assert.equal(await completeCallback(q("code=c"), p), "/login?error=link_invalid");
  assert.deepEqual(calls.claims, []);
});

test("A2-CB-06 a claim that fails or throws does not block sign-in: the user lands on onboarding to retry", async () => {
  const failing = [
    async () => ({ kind: "unavailable" }) as OnboardingState,
    async (): Promise<OnboardingState> => {
      throw new Error("network");
    },
  ];
  for (const claim of failing) {
    const { ports: p } = ports({ claim });
    assert.equal(await completeCallback(q("code=c"), p), AFTER_VERIFY);
  }
});

test("A2-CB-08 the facade's landing is kept when same-site and dropped when not", async () => {
  for (const [redirect, expected] of [["/update-password", "/update-password"], ["/api-keys", "/api-keys"], ["//evil.example", AFTER_VERIFY], ["https://evil.example", AFTER_VERIFY], ["/\\evil", AFTER_VERIFY]]) {
    const { ports: p } = ports({}, ok({ session: SESSION, redirect }));
    assert.equal(await completeCallback(q("code=c"), p), expected, redirect);
  }
});

test("A2-NEXT-01 safeNext refuses protocol-relative, backslash and absolute targets", () => {
  assert.equal(safeNext("/models"), "/models");
  assert.equal(safeNext("/usage?range=7d"), "/usage?range=7d");
  for (const evil of ["//evil", "/\\evil", "\\\\evil", "https://evil", "javascript:alert(1)", "", null, undefined, "/a\tb"]) {
    assert.equal(safeNext(evil as string | null | undefined), "/models", String(evil));
  }
  assert.equal(safeNext("//evil", "/welcome"), "/welcome");
});

test("A2-NEXT-02 safeNext has one definition, the strict one in app/(auth)/flow.ts (WR-A2-3)", () => {
  // Catches: a second, looser same-site check (the old `lib/utils` one let `/\\evil` through)
  // coming back for a new caller to import by mistake.
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const sources = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
      entry.name === "node_modules" || entry.name.startsWith(".")
        ? []
        : entry.isDirectory()
          ? sources(join(dir, entry.name))
          : /\.(ts|tsx|mjs)$/.test(entry.name) && !entry.name.endsWith(".test.ts")
            ? [join(dir, entry.name)]
            : [],
    );
  const defining = ["app", "lib", "components"]
    .flatMap((dir) => sources(join(root, dir)))
    .filter((file) => /\b(?:function\s+safeNext\b|(?:const|let|var)\s+safeNext\s*=)/.test(readFileSync(file, "utf8")))
    .map((file) => relative(root, file));
  assert.deepEqual(defining, [join("app", "(auth)", "flow.ts")]);
});

// --------------------------------------------------------------------------- the claim ---

test("A2-GRANT-01 granted and replayed are both credited, with the exact amount; only the first is `first`", () => {
  assert.deepEqual(claimOutcome(ok(CLAIM)), { kind: "credited", first: true, amount: "10000.00000000", grantedAt: GRANTED_AT });
  const replay = claimOutcome(ok({ ...CLAIM, status: "replayed" as const }));
  assert.equal(replay.kind, "credited");
  assert.equal(replay.kind === "credited" && replay.first, false, "a replay must not be shown as a new grant");
});

test("A2-GRANT-02 every denial is its own state; none of them is credited", () => {
  assert.deepEqual(claimOutcome(ok({ status: "unverified" as const })), { kind: "unverified" });
  for (const reason of ["identity_reused", "rollout_hold", "retired"] as const) {
    assert.deepEqual(claimOutcome(ok({ status: reason })), { kind: "held", reason });
  }
});

test("A2-GRANT-03 a failed call, an unknown status or an inexact or relabelled amount is unavailable, never a guessed credit", () => {
  const unavailable = { kind: "unavailable" };
  assert.deepEqual(claimOutcome(refused("dependency_unavailable", 503)), unavailable);
  assert.deepEqual(claimOutcome(down), unavailable);
  assert.deepEqual(claimOutcome(ok({ status: "granted_twice" } as never)), unavailable);
  assert.deepEqual(claimOutcome(ok({ ...CLAIM, granted_at: null })), unavailable, "a grant without its instant");
  assert.deepEqual(claimOutcome(ok({ ...CLAIM, credit: { amount: "10000.00000000", unit: "USD" as const } })), unavailable, "never relabelled");
  for (const amount of ["10000", "1e4", "10000.000000001", "ten"]) {
    assert.deepEqual(claimOutcome(ok({ ...CLAIM, credit: { amount, unit: "CREDIT" as const } })), unavailable, `amount ${amount}`);
  }
});

test("A2-GRANT-04 the onboarding action claims for the session only; no session claims nothing", async () => {
  let claimed = 0;
  const claim = async () => {
    claimed += 1;
    return { kind: "unverified" } as OnboardingState;
  };
  assert.deepEqual(await onboardingFor(async () => null, claim), { kind: "signed_out" });
  assert.equal(claimed, 0);
  assert.deepEqual(await onboardingFor(async () => "eyJ.a.b", claim), { kind: "unverified" });
  assert.equal(claimed, 1);
  // A throwing port is an unavailable answer the page can offer a retry for, not a 500.
  assert.deepEqual(await onboardingFor(async () => "eyJ.a.b", async () => { throw new Error("boom"); }), { kind: "unavailable" });
});

test("A2-GRANT-05 the claim is one POST /console/v1/signup-grant/claim as the session, with no body and no user id", async () => {
  const { api, sent } = recorded(() => json(200, CLAIM));
  assert.equal((await claimGrant(api)).kind, "credited");
  assert.deepEqual(sent.map((s) => [s.method, s.path, s.body]), [["POST", "/console/v1/signup-grant/claim", null]]);
  const failed = recorded(() => { throw new Error("down"); });
  assert.deepEqual(await claimGrant(failed.api), { kind: "unavailable" });
});

// ---------------------------------------------------------------------------- the wallet ---

test("A2-BAL-01 the onboarding balance is the wallet's exact CREDIT figure", () => {
  assert.deepEqual(walletBalance(ok(CREDITS)), { kind: "available", available: "9999.50000000", grantedAt: GRANTED_AT });
});

test("A2-BAL-02 no wallet yet is `not_issued`, stated explicitly, never shown as a confirmed zero balance", () => {
  assert.deepEqual(walletBalance(ok({ ...CREDITS, wallet_id: null })), { kind: "not_issued" });
});

test("A2-BAL-03 a failed, wrong-unit or inexact read is unavailable, never a zero or a float", () => {
  assert.deepEqual(walletBalance(refused("forbidden", 403)), { kind: "unavailable" });
  assert.deepEqual(walletBalance(down), { kind: "unavailable" });
  assert.deepEqual(walletBalance(ok({ ...CREDITS, available: { amount: "10.00000000", unit: "USD" as never } })), { kind: "unavailable" }, "a USD figure is never a CREDIT one");
  for (const amount of ["ten", "10000", "1e4", "10000.000000001"]) {
    assert.deepEqual(walletBalance(ok({ ...CREDITS, available: { amount, unit: "CREDIT" as const } })), { kind: "unavailable" }, `available ${amount}`);
  }
});

test("A2-BAL-04 the /welcome read: one GET /console/v1/credits; a failed or thrown read is never a confirmed zero", async () => {
  const good = recorded(() => json(200, CREDITS));
  assert.deepEqual(await welcomeWallet(good.api), { kind: "available", available: "9999.50000000", grantedAt: GRANTED_AT });
  assert.deepEqual(good.sent.map((s) => [s.method, s.path]), [["GET", "/console/v1/credits"]], "one read per render");
  assert.deepEqual(await welcomeWallet(recorded(() => json(503, envelope("dependency_unavailable"))).api), { kind: "unavailable" });
  assert.deepEqual(await welcomeWallet(recorded(() => json(200, { ...CREDITS, wallet_id: null })).api), { kind: "not_issued" });
  assert.deepEqual(await welcomeWallet(recorded(() => { throw new Error("fetch failed"); }).api), { kind: "unavailable" });
});

// ------------------------------------------------------------------------ auth failures ---

test("A2-ENUM-03 the facade's codes are the copy's keys; a dead session is an expired link; nothing else leaks", () => {
  const error = (code: string, status: number) => {
    const r = refused(code, status);
    return r.ok ? assert.fail("a refusal") : r.error;
  };
  for (const code of Object.keys(FAILURE_COPY)) assert.equal(facadeFailure(error(code, 400)), code);
  assert.equal(facadeFailure(error("unauthenticated", 401)), "link_expired", "an ended recovery session offers a new link");
  assert.equal(facadeFailure({ kind: "openai", status: 429, code: null, message: "x" }), "rate_limited", "a bare 429 is a rate limit");
  assert.equal(facadeFailure(error("something_new", 500)), "unavailable");
  assert.equal(facadeFailure({ kind: "unavailable", status: null, reason: "network" }), "unavailable");
  assert.equal(facadeFailure(error("toString", 400)), "unavailable", "a prototype key is not a code");
  for (const copy of Object.values(FAILURE_COPY)) assert.ok(copy.length > 0 && !copy.includes("SECRET"), copy);
});

test("A2-ENUM-04 an email form settles `sent` on any 2xx (the facade is enumeration-safe), and reports failures truthfully", () => {
  assert.equal(settled(ok({ status: "sent" })), "sent");
  assert.equal(settled(refused("rate_limited", 429)), "rate_limited", "a rate limit is not 'sent'");
  assert.equal(settled(down), "unavailable", "no answer is not 'sent'");
});

test("A2-NOTICE-01 the login page shows only known notices; anything else in ?error= is ignored", () => {
  assert.match(loginNotice("link_expired") ?? "", /expired/i);
  assert.ok(loginNotice("link_invalid"));
  assert.equal(loginNotice("Your account is locked, call us"), null);
  assert.equal(loginNotice(null), null);
  assert.equal(loginNotice("toString"), null, "a prototype key is not a notice");
});

test("A2-SIGNIN-01 sign-in claims the grant once; only a replayed grant continues to next, every other outcome lands on /welcome", async () => {
  const NEXT = "/usage";
  const credited = (first: boolean) => ({ kind: "credited", first, amount: "10000.00000000", grantedAt: GRANTED_AT }) as OnboardingState;
  const cases: [string, () => Promise<OnboardingState>, string][] = [
    ["a first grant is announced on onboarding", async () => credited(true), AFTER_VERIFY],
    ["a replayed grant continues to next", async () => credited(false), NEXT],
    ["an unavailable claim lands on the retry", async () => ({ kind: "unavailable" }), AFTER_VERIFY],
    ["a held grant lands on onboarding", async () => ({ kind: "held", reason: "rollout_hold" }), AFTER_VERIFY],
    ["an unverified answer lands on onboarding", async () => ({ kind: "unverified" }), AFTER_VERIFY],
    ["a lost session lands on onboarding", async () => ({ kind: "signed_out" }), AFTER_VERIFY],
    [
      "a thrown claim still signs in, onto the retry",
      async () => {
        throw new Error("network");
      },
      AFTER_VERIFY,
    ],
  ];
  for (const [name, answer, expected] of cases) {
    let claims = 0;
    const target = await afterSignIn(() => {
      claims += 1;
      return answer();
    }, NEXT);
    assert.equal(claims, 1, `${name}: exactly one claim`);
    assert.equal(target, expected, name);
  }
});

const ORIGIN = "https://app.example";

test("A2-EMAIL-01 signup and reset send one facade request each, with links through /auth/callback and the PKCE challenge and CAPTCHA token forwarded", async () => {
  const verifyLink = `${ORIGIN}/auth/callback?next=/welcome`;
  const signup = recorded(() => json(200, { status: "sent" }));
  assert.equal(await requestSignup(signup.api, "a@example.test", "pw-123456", ORIGIN, { codeChallenge: "c".repeat(43), captchaToken: "tok" }), "sent");
  assert.deepEqual(signup.sent.map((s) => [s.method, s.path, s.body, s.auth]), [
    ["POST", "/auth/v1/sign-up", { email: "a@example.test", password: "pw-123456", redirect_to: verifyLink, code_challenge: "c".repeat(43), captcha_token: "tok" }, null],
  ]);
  const bare = recorded(() => json(200, { status: "sent" }));
  await requestSignup(bare.api, "a@example.test", "pw", ORIGIN);
  assert.deepEqual(bare.sent[0].body, { email: "a@example.test", password: "pw", redirect_to: verifyLink }, "nothing optional is invented");
  assert.equal(await requestSignup(recorded(() => json(422, envelope("weak_password"))).api, "a@example.test", "pw", ORIGIN), "weak_password");
  assert.equal(await requestSignup(recorded(() => { throw new Error("down"); }).api, "a@example.test", "pw", ORIGIN), "unavailable");

  const reset = recorded(() => json(200, { status: "sent" }));
  assert.equal(await requestReset(reset.api, "a@example.test", ORIGIN, { codeChallenge: "d".repeat(43) }), "sent");
  assert.deepEqual(reset.sent.map((s) => [s.method, s.path, s.body]), [
    ["POST", "/auth/v1/recovery", { email: "a@example.test", redirect_to: `${ORIGIN}/auth/callback?next=/update-password`, code_challenge: "d".repeat(43) }],
  ]);
  assert.equal(await requestReset(recorded(() => json(429, envelope("rate_limited"))).api, "a@example.test", ORIGIN), "rate_limited", "forgot-password must not say 'sent' when rate-limited");
});

test("A2-EMAIL-02 the links those emails carry pass through the callback, which claims and lands where the facade says", async () => {
  for (const [link, landing] of [[verifyRedirect(ORIGIN), AFTER_VERIFY], [resetRedirect(ORIGIN), "/update-password"]]) {
    const url = new URL(link);
    assert.equal(url.origin + url.pathname, `${ORIGIN}/auth/callback`, `${link} must pass through the callback`);
    url.searchParams.set("code", "pkce-1");
    const { ports: p, calls } = ports({}, ok({ session: SESSION, redirect: url.searchParams.get("next") as string }));
    assert.equal(await completeCallback(url.searchParams, p), landing);
    assert.deepEqual(calls.claims, [USER]);
  }
});

test("A2-EMAIL-03 resend sends one facade request with the verify link (PKCE, CAPTCHA forwarded) and never claims a send it did not make", async () => {
  const resend = recorded(() => json(200, { status: "sent" }));
  assert.equal(await requestResend(resend.api, "a@example.test", ORIGIN, { codeChallenge: "e".repeat(43), captchaToken: "tok" }), "sent");
  assert.deepEqual(resend.sent.map((s) => [s.method, s.path, s.body, s.auth]), [
    ["POST", "/auth/v1/resend", { email: "a@example.test", redirect_to: `${ORIGIN}/auth/callback?next=/welcome`, code_challenge: "e".repeat(43), captcha_token: "tok" }, null],
  ]);
  assert.equal(await requestResend(recorded(() => json(429, envelope("rate_limited"))).api, "a@example.test", ORIGIN), "rate_limited");
  assert.equal(await requestResend(recorded(() => { throw new Error("down"); }).api, "a@example.test", ORIGIN), "unavailable");
});

test("LR02-FORM-01 a required challenge with no widget configured closes the email forms honestly; otherwise they are open", () => {
  const availability = (captcha: boolean) =>
    ok({
      captcha_required: captcha,
      sign_in: { state: "configured" as const }, sign_up: { state: "configured" as const },
      recovery: { state: "configured" as const }, signup_grant: { state: "configured" as const },
    });
  assert.equal(captchaGate(availability(true)), "unconfigured");
  assert.equal(captchaGate(availability(false)), "off");
  assert.equal(captchaGate(down), "off", "an unknown requirement leaves the decision to the auth service");
  assert.equal(captchaGate(null), "off");
  assert.match(CAPTCHA_UNAVAILABLE, /cannot be sent/);
});
