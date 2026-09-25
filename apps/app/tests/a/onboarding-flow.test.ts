// node --test "tests/**/*.test.ts"
//
// A2: public verified onboarding and recovery. The decisions that matter (what a callback does,
// which user a grant is claimed for, what a claim or wallet read MEANS for the page, and what an
// auth failure is allowed to say) live in `app/(auth)/flow.ts` as pure functions with injected
// ports, so they run here without Next or Supabase. Each case names the broken behaviour it catches.
import assert from "node:assert/strict";
import test from "node:test";

import {
  AFTER_VERIFY,
  FAILURE_COPY,
  SIGNUP_CAMPAIGN,
  afterSignIn,
  authFailure,
  claimOutcome,
  completeCallback,
  emailSettled,
  loginNotice,
  onboardingFor,
  requestReset,
  requestResend,
  requestSignup,
  resetRedirect,
  safeNext,
  signupSettled,
  verifyRedirect,
  walletBalance,
  welcomeWallet,
  type CallbackPorts,
  type EmailAuth,
  type OnboardingState,
} from "../../app/(auth)/flow.ts";

const USER = "a2a2a2a2-0000-4000-8000-000000000001";
const GRANT_ROW = {
  status: "granted",
  user_id: USER,
  wallet_id: "a2a2a2a2-0000-4000-8000-000000000002",
  ledger_operation_id: "a2a2a2a2-0000-4000-8000-000000000003",
  amount: "10000.00000000",
  granted_at: "2026-09-25T12:00:00+00:00",
};

type Calls = { exchange: string[]; verify: [string, string][]; claims: string[] };

function ports(overrides: Partial<CallbackPorts> = {}, calls: Calls = { exchange: [], verify: [], claims: [] }) {
  const base: CallbackPorts = {
    exchangeCode: async (code) => {
      calls.exchange.push(code);
      return { error: null };
    },
    verifyOtp: async (tokenHash, type) => {
      calls.verify.push([tokenHash, type]);
      return { error: null };
    },
    verifiedUserId: async () => USER,
    claim: async (userId) => {
      calls.claims.push(userId);
      return { kind: "credited", first: true, amount: "10000.00000000", grantedAt: GRANT_ROW.granted_at } as OnboardingState;
    },
  };
  return { ports: { ...base, ...overrides }, calls };
}

const q = (text: string) => new URLSearchParams(text);

// ------------------------------------------------------------------------------ callback ---

test("A2-CB-01 a link error is a fixed code; the provider's description is never reflected into the page", async () => {
  const { ports: p, calls } = ports();
  const phishing = "Your account is locked. Call +1 555 0100";
  const expired = await completeCallback(
    q(`error=access_denied&error_code=otp_expired&error_description=${encodeURIComponent(phishing)}`),
    p,
  );
  assert.equal(expired, "/login?error=link_expired", "an expired email link must say so, as a code");
  const other = await completeCallback(q(`error=server_error&error_description=${encodeURIComponent(phishing)}`), p);
  assert.equal(other, "/login?error=link_invalid", "any other link failure is the generic invalid-link code");
  for (const target of [expired, other]) assert.ok(!target.includes("locked"), `reflected text in ${target}`);
  assert.deepEqual(calls.claims, [], "a failed link claims nothing");
});

test("A2-CB-02 a callback without a code or token is an invalid link, not a crash or a silent sign-in", async () => {
  const { ports: p, calls } = ports();
  assert.equal(await completeCallback(q(""), p), "/login?error=link_invalid");
  assert.equal(await completeCallback(q("token_hash=abc"), p), "/login?error=link_invalid", "a token without a type");
  assert.equal(await completeCallback(q("token_hash=abc&type=sso"), p), "/login?error=link_invalid", "an unknown type");
  assert.deepEqual(calls, { exchange: [], verify: [], claims: [] });
});

test("A2-CB-03 a verified callback claims the grant ONCE, for the server-verified user, never an id from the URL", async () => {
  const { ports: p, calls } = ports();
  const target = await completeCallback(q(`code=pkce-1&user_id=ffffffff-0000-4000-8000-00000000dead&next=/welcome`), p);
  assert.equal(target, AFTER_VERIFY);
  assert.deepEqual(calls.exchange, ["pkce-1"]);
  assert.deepEqual(calls.claims, [USER], "exactly one claim, for the id the auth server verified");
});

test("A2-CB-04 the token-hash form verifies the OTP with its type, then claims", async () => {
  const { ports: p, calls } = ports();
  const target = await completeCallback(q("token_hash=th-1&type=signup"), p);
  assert.equal(target, AFTER_VERIFY, "a signup verification lands on the onboarding page by default");
  assert.deepEqual(calls.verify, [["th-1", "signup"]]);
  assert.deepEqual(calls.claims, [USER]);
});

test("A2-CB-05 an expired or reused code is the expired-link state and claims nothing", async () => {
  for (const code of ["otp_expired", "flow_state_expired", "flow_state_not_found"]) {
    const { ports: p, calls } = ports({ exchangeCode: async () => ({ error: { code, status: 403, message: "raw" } }) });
    assert.equal(await completeCallback(q("code=old"), p), "/login?error=link_expired", code);
    assert.deepEqual(calls.claims, [], `${code}: no claim after a failed exchange`);
  }
  const { ports: p } = ports({ verifyOtp: async () => ({ error: { code: "bad_code_verifier", status: 400, message: "raw" } }) });
  assert.equal(await completeCallback(q("token_hash=x&type=email"), p), "/login?error=link_invalid");
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

test("A2-CB-07 a session that cannot be read after the exchange is an invalid link and claims nothing", async () => {
  const { ports: p, calls } = ports({ verifiedUserId: async () => null });
  assert.equal(await completeCallback(q("code=c"), p), "/login?error=link_invalid");
  assert.deepEqual(calls.claims, []);
});

test("A2-CB-08 recovery goes to set-a-new-password; a safe next is kept and an unsafe one is dropped", async () => {
  const { ports: p } = ports();
  assert.equal(await completeCallback(q("token_hash=r&type=recovery"), p), "/update-password");
  assert.equal(await completeCallback(q("code=c&next=/update-password"), p), "/update-password");
  assert.equal(await completeCallback(q("code=c&next=/api-keys"), p), "/api-keys");
  for (const evil of ["//evil.example", "/\\evil.example", "https://evil.example", "/%5Cevil", "/ok\n"]) {
    assert.equal(await completeCallback(q(`code=c&next=${encodeURIComponent(evil)}`), p), AFTER_VERIFY, evil);
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

// --------------------------------------------------------------------------- the claim ---

test("A2-GRANT-01 granted and replayed are both credited, with the exact amount; only the first is `first`", () => {
  assert.deepEqual(claimOutcome([GRANT_ROW], null), {
    kind: "credited",
    first: true,
    amount: "10000.00000000",
    grantedAt: GRANT_ROW.granted_at,
  });
  const replay = claimOutcome([{ ...GRANT_ROW, status: "replayed" }], null);
  assert.equal(replay.kind, "credited");
  assert.equal(replay.kind === "credited" && replay.first, false, "a replay must not be shown as a new grant");
  // supabase-js returns a single object for a one-row set in some call shapes.
  assert.equal(claimOutcome(GRANT_ROW, null).kind, "credited");
});

test("A2-GRANT-02 every denial is its own state; none of them is credited", () => {
  assert.deepEqual(claimOutcome([{ status: "unverified" }], null), { kind: "unverified" });
  for (const reason of ["identity_reused", "rollout_hold", "retired"] as const) {
    assert.deepEqual(claimOutcome([{ status: reason }], null), { kind: "held", reason });
  }
});

test("A2-GRANT-03 an error, no row, an unknown status or an inexact amount is unavailable, never a guessed credit", () => {
  const unavailable = { kind: "unavailable" };
  assert.deepEqual(claimOutcome(null, { code: "55000", message: "maintenance: signup_grant is not enabled" }), unavailable);
  assert.deepEqual(claimOutcome(null, { code: "PGRST202", message: "no function" }), unavailable);
  assert.deepEqual(claimOutcome([], null), unavailable);
  assert.deepEqual(claimOutcome(null, null), unavailable);
  assert.deepEqual(claimOutcome([GRANT_ROW], { code: "57014", message: "canceled" }), unavailable, "an error beside a row is still an error");
  assert.deepEqual(claimOutcome([{ status: "granted_twice" }], null), unavailable);
  assert.deepEqual(claimOutcome([GRANT_ROW, GRANT_ROW], null), unavailable, "two rows is not one grant");
  for (const amount of [10000, "10000", "1e4", null, "10000.000000001"]) {
    assert.deepEqual(claimOutcome([{ ...GRANT_ROW, amount }], null), unavailable, `amount ${String(amount)}`);
  }
});

test("A2-GRANT-04 the onboarding action claims for the session user only; no session claims nothing", async () => {
  const claimed: string[] = [];
  const claim = async (id: string) => {
    claimed.push(id);
    return { kind: "unverified" } as OnboardingState;
  };
  assert.deepEqual(await onboardingFor(async () => null, claim), { kind: "signed_out" });
  assert.deepEqual(claimed, []);
  assert.deepEqual(await onboardingFor(async () => USER, claim), { kind: "unverified" });
  assert.deepEqual(claimed, [USER]);
  // A throwing port is an unavailable answer the page can offer a retry for, not a 500.
  const broken = async (): Promise<OnboardingState> => {
    throw new Error("boom");
  };
  assert.deepEqual(await onboardingFor(async () => USER, broken), { kind: "unavailable" });
  assert.equal(typeof SIGNUP_CAMPAIGN, "string");
  assert.ok(SIGNUP_CAMPAIGN.length > 0 && SIGNUP_CAMPAIGN.length <= 100, "0006's campaign_version CHECK");
});

// ---------------------------------------------------------------------------- the wallet ---

test("A2-BAL-01 the onboarding balance is the wallet's exact CREDIT figure", () => {
  assert.deepEqual(
    walletBalance(
      { wallet_id: GRANT_ROW.wallet_id, unit: "CREDIT", available: "9999.50000000", signup_granted_at: GRANT_ROW.granted_at },
      null,
    ),
    { kind: "available", available: "9999.50000000", grantedAt: GRANT_ROW.granted_at },
  );
});

test("A2-BAL-02 no wallet yet is `not_issued`, stated explicitly, never shown as a confirmed zero balance", () => {
  const row = { wallet_id: null, unit: "CREDIT", available: "0.00000000", signup_granted_at: null };
  assert.deepEqual(walletBalance(row, null), { kind: "not_issued" });
});

test("A2-BAL-03 a failed, empty, wrong-unit or inexact read is unavailable, never a zero or a float", () => {
  const ok = { wallet_id: GRANT_ROW.wallet_id, unit: "CREDIT", available: "10000.00000000", signup_granted_at: null };
  assert.deepEqual(walletBalance(null, { code: "42501", message: "denied" }), { kind: "unavailable" });
  assert.deepEqual(walletBalance(null, null), { kind: "unavailable" });
  assert.deepEqual(walletBalance({ ...ok, unit: "USD" }, null), { kind: "unavailable" }, "a USD figure is never a CREDIT one");
  assert.deepEqual(walletBalance({ ...ok, available: 10000 }, null), { kind: "unavailable" }, "a JSON number is not exact");
  for (const available of ["ten", "10000", "1e4", "10000.000000001"]) {
    assert.deepEqual(walletBalance({ ...ok, available }, null), { kind: "unavailable" }, `available ${available}`);
  }
  assert.deepEqual(walletBalance([ok], null).kind, "available", "an rpc array of one row reads the same");
});

// ------------------------------------------------------------------------ auth failures ---

test("A2-ENUM-01 wrong password and unknown address read identically", () => {
  const unknown = authFailure({ code: "user_not_found", status: 400, message: "User not found" });
  const wrong = authFailure({ code: "invalid_credentials", status: 400, message: "Invalid login credentials" });
  assert.equal(unknown, wrong);
  assert.equal(FAILURE_COPY[wrong], FAILURE_COPY.invalid_credentials);
});

test("A2-ENUM-02 signing up with an address that already has an account looks exactly like a new signup", () => {
  assert.equal(signupSettled(null), "sent");
  assert.equal(signupSettled({ code: "user_already_exists", status: 422, message: "User already registered" }), "sent");
  assert.equal(signupSettled({ code: "email_exists", status: 422, message: "exists" }), "sent");
  assert.equal(signupSettled({ code: "weak_password", status: 422, message: "weak" }), "weak_password");
});

test("A2-ENUM-03 rate limits, expired links and outages are actionable codes; the raw server message never is the copy", () => {
  assert.equal(authFailure({ code: "over_request_rate_limit", status: 429, message: "x" }), "rate_limited");
  assert.equal(authFailure({ code: "over_email_send_rate_limit", status: 429, message: "x" }), "rate_limited");
  assert.equal(authFailure({ status: 429, message: "x" }), "rate_limited", "a 429 without a code is still a rate limit");
  assert.equal(authFailure({ code: "email_not_confirmed", status: 400, message: "x" }), "email_not_confirmed");
  assert.equal(authFailure({ code: "otp_expired", status: 403, message: "x" }), "link_expired");
  assert.equal(authFailure({ name: "AuthSessionMissingError", message: "Auth session missing!" }), "link_expired");
  assert.equal(authFailure({ name: "AuthRetryableFetchError", status: 0, message: "Failed to fetch" }), "unavailable");
  assert.equal(authFailure({ code: "something_new", status: 500, message: "SECRET internal detail" }), "unavailable");
  for (const copy of Object.values(FAILURE_COPY)) {
    assert.ok(copy.length > 0 && !copy.includes("SECRET"), copy);
  }
});

test("A2-NOTICE-01 the login page shows only known notices; anything else in ?error= is ignored", () => {
  assert.match(loginNotice("link_expired") ?? "", /expired/i);
  assert.ok(loginNotice("link_invalid"));
  assert.equal(loginNotice("Your account is locked, call us"), null);
  assert.equal(loginNotice(null), null);
  assert.equal(loginNotice("toString"), null, "a prototype key is not a notice");
});

// ------------------------------------------------------------------- fix round (review) ---

test("A2-ENUM-04 resend and reset read an unknown address as sent, and report rate limits and outages truthfully", () => {
  assert.equal(emailSettled(null), "sent");
  assert.equal(emailSettled({ code: "user_not_found", status: 400, message: "User not found" }), "sent", "an unknown address must not be revealed");
  assert.equal(emailSettled({ code: "invalid_credentials", status: 400, message: "x" }), "sent");
  assert.equal(emailSettled({ code: "over_email_send_rate_limit", status: 429, message: "x" }), "rate_limited", "a rate limit is not 'sent'");
  assert.equal(emailSettled({ status: 429, message: "x" }), "rate_limited");
  assert.equal(emailSettled({ status: 0 }), "unavailable", "no answer is not 'sent'");
});

test("A2-BAL-04 the /welcome read: exact balance, not issued, or unavailable — a failed or thrown read is never a confirmed zero", async () => {
  const ok = { wallet_id: GRANT_ROW.wallet_id, unit: "CREDIT", available: "10000.00000000", signup_granted_at: GRANT_ROW.granted_at };
  let reads = 0;
  const answer = (data: unknown, error: { code: string; message: string } | null) => async () => {
    reads += 1;
    return { data, error };
  };
  assert.deepEqual(await welcomeWallet(answer([ok], null)), { kind: "available", available: "10000.00000000", grantedAt: GRANT_ROW.granted_at });
  assert.equal(reads, 1, "one read per render");
  assert.deepEqual(await welcomeWallet(answer(null, { code: "42501", message: "denied" })), { kind: "unavailable" });
  assert.deepEqual(await welcomeWallet(answer([ok], { code: "57014", message: "canceled" })), { kind: "unavailable" }, "an error beside a row");
  assert.deepEqual(await welcomeWallet(answer([{ ...ok, wallet_id: null, available: "0.00000000" }], null)), { kind: "not_issued" });
  assert.deepEqual(await welcomeWallet(answer([{ ...ok, available: 10000 }], null)), { kind: "unavailable" }, "a float is not exact");
  const thrown = await welcomeWallet(async () => {
    throw new Error("fetch failed");
  });
  assert.deepEqual(thrown, { kind: "unavailable" }, "a thrown read is a retry state, not a crash or a zero");
});

test("A2-SIGNIN-01 sign-in claims the grant once; only a replayed grant continues to next, every other outcome lands on /welcome", async () => {
  const NEXT = "/usage";
  const credited = (first: boolean): OnboardingState => ({ kind: "credited", first, amount: "10000.00000000", grantedAt: GRANT_ROW.granted_at });
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
type Sent = { method: string; args: unknown[] };

function fakeAuth(error: Record<string, unknown> | null | "throw" = null) {
  const sent: Sent[] = [];
  const reply = async (method: string, args: unknown[]) => {
    sent.push({ method, args });
    if (error === "throw") throw new Error("fetch failed");
    return { data: null, error };
  };
  const auth: EmailAuth = {
    signUp: (...args) => reply("signUp", args),
    resend: (...args) => reply("resend", args),
    resetPasswordForEmail: (...args) => reply("resetPasswordForEmail", args),
  };
  return { auth, sent };
}

test("A2-EMAIL-01 signup, resend and reset send one request each, with links through /auth/callback, and settle without enumeration", async () => {
  const verifyLink = `${ORIGIN}/auth/callback?next=/welcome`;
  const signup = fakeAuth();
  assert.equal(await requestSignup(signup.auth, "a@example.test", "pw-123456", ORIGIN), "sent");
  assert.deepEqual(signup.sent, [
    { method: "signUp", args: [{ email: "a@example.test", password: "pw-123456", options: { emailRedirectTo: verifyLink } }] },
  ]);
  assert.equal(await requestSignup(fakeAuth({ code: "user_already_exists", status: 422 }).auth, "a@example.test", "pw", ORIGIN), "sent");
  assert.equal(await requestSignup(fakeAuth({ code: "weak_password", status: 422 }).auth, "a@example.test", "pw", ORIGIN), "weak_password");
  assert.equal(await requestSignup(fakeAuth("throw").auth, "a@example.test", "pw", ORIGIN), "unavailable");

  const resend = fakeAuth();
  assert.equal(await requestResend(resend.auth, "a@example.test", ORIGIN), "sent");
  assert.deepEqual(resend.sent, [
    { method: "resend", args: [{ type: "signup", email: "a@example.test", options: { emailRedirectTo: verifyLink } }] },
  ]);
  assert.equal(await requestResend(fakeAuth({ code: "user_not_found", status: 400 }).auth, "b@example.test", ORIGIN), "sent");
  assert.equal(await requestResend(fakeAuth({ code: "over_email_send_rate_limit", status: 429 }).auth, "a@example.test", ORIGIN), "rate_limited");

  const reset = fakeAuth();
  assert.equal(await requestReset(reset.auth, "a@example.test", ORIGIN), "sent");
  assert.deepEqual(reset.sent, [
    { method: "resetPasswordForEmail", args: ["a@example.test", { redirectTo: `${ORIGIN}/auth/callback?next=/update-password` }] },
  ]);
  assert.equal(await requestReset(fakeAuth({ code: "user_not_found", status: 400 }).auth, "b@example.test", ORIGIN), "sent");
  assert.equal(
    await requestReset(fakeAuth({ code: "over_email_send_rate_limit", status: 429 }).auth, "a@example.test", ORIGIN),
    "rate_limited",
    "forgot-password must not say 'sent' when rate-limited",
  );
  assert.equal(await requestReset(fakeAuth("throw").auth, "a@example.test", ORIGIN), "unavailable");
});

test("A2-EMAIL-02 the links those emails carry, completed by the callback, claim the grant and land where the brief says", async () => {
  const opened = async (link: string) => {
    const url = new URL(link);
    assert.equal(url.origin + url.pathname, `${ORIGIN}/auth/callback`, `${link} must pass through the callback`);
    url.searchParams.set("code", "pkce-1");
    const { ports: p, calls } = ports();
    return { target: await completeCallback(url.searchParams, p), calls };
  };
  const verified = await opened(verifyRedirect(ORIGIN));
  assert.equal(verified.target, AFTER_VERIFY, "a verification link lands on onboarding");
  assert.deepEqual(verified.calls.claims, [USER], "and claims the grant at verification");
  const recovered = await opened(resetRedirect(ORIGIN));
  assert.equal(recovered.target, "/update-password", "a recovery link reaches set-a-new-password");
});
