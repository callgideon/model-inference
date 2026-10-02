/**
 * A2 over infrx-api (AP-09 09b): the decisions behind public signup, verification, sign-in and
 * recovery, as pure functions over the generated client.
 *
 * Every identity-provider call is the auth facade's (`/auth/v1/*`): it holds the enumeration-safe
 * outcomes (an existing address signs up as `sent`, an unknown one recovers as `sent`, a wrong
 * password reads like an unknown address) and answers each failure as one fixed code. Nothing here
 * imports Next or an SDK, so `node --test` runs it (tests/a). The route, the pages and the server
 * actions (`./session.ts`, `./auth-actions.ts`) are thin wiring around it:
 *
 * - `completeCallback` — what an email link does: the facade verifies it, the App stores the
 *   session, then claims the one-time grant (`POST /console/v1/signup-grant/claim`, as the verified
 *   session - never an id from the URL), then lands. A claim that fails does not block sign-in;
 *   onboarding offers the retry.
 * - `claimOutcome` / `walletBalance` — what the grant claim and `/console/v1/credits` mean for the
 *   page. An error or an inexact figure is `unavailable`, never a guessed credit or a confirmed zero.
 * - `facadeFailure` / `FAILURE_COPY` / `loginNotice` — fixed copy for every auth failure. The raw
 *   server text is never shown.
 */

import type { components } from "@infrx/api-client/consumer";
import type { ApiError, Result as ApiResult } from "@infrx/api-client/transport";
import type { ConsumerApi } from "../../lib/api/index.ts";
import { parseCredit, type Credit } from "../../lib/contracts/v2/money-units.ts";

type S = components["schemas"];

/** Where a verified email link lands by default: the onboarding page with the balance. */
export const AFTER_VERIFY = "/welcome";
/** Client-side hint only; the auth service's own policy is the authority (P-05). */
export const MIN_PASSWORD_LENGTH = 8;

/**
 * A same-site path to redirect to, or `fallback`. A
 * backslash (`/\evil` — browsers read it as `//evil`) or any whitespace/control character is
 * refused too, so the login round trip cannot become an open redirect.
 */
export function safeNext(next: string | null | undefined, fallback = "/models"): string {
  if (typeof next !== "string" || !/^\/(?!\/)[^\\\s\u0000-\u001f\u007f]*$/.test(next)) return fallback;
  if (/%5c|%2f%2f/i.test(next)) return fallback;
  return next;
}

// ------------------------------------------------------------------------ auth failures ---

export type AuthFailure =
  | "invalid_credentials"
  | "email_not_confirmed"
  | "rate_limited"
  | "weak_password"
  | "same_password"
  | "invalid_email"
  | "signup_closed"
  | "email_unavailable"
  | "link_expired"
  | "unavailable";

export const FAILURE_COPY: Readonly<Record<AuthFailure, string>> = Object.freeze({
  invalid_credentials: "That email and password do not match an account.",
  email_not_confirmed: "Verify your email address first. We can send you a new verification link.",
  rate_limited: "Too many attempts. Wait a minute, then try again.",
  weak_password: `Choose a stronger password: at least ${MIN_PASSWORD_LENGTH} characters, not a common one.`,
  same_password: "Choose a password different from your current one.",
  invalid_email: "Enter a valid email address.",
  signup_closed: "New accounts are not being accepted right now. Try again later.",
  email_unavailable: "We could not send an email to this address right now. Try again later.",
  link_expired: "This link has expired or was already used. Request a new one.",
  unavailable: "Something went wrong on our side. Try again in a moment.",
});

/**
 * The facade's failure as the App's fixed failure. The facade answers one of these codes already;
 * a session that ended (`unauthenticated`, a recovery link used up) is `link_expired`, a bare 429 is
 * rate limited, and anything else - an outage, an unreadable answer - is `unavailable`.
 */
export function facadeFailure(error: ApiError): AuthFailure {
  const code = error.kind === "error" ? error.code : null;
  if (code === "unauthenticated") return "link_expired";
  if (code !== null && Object.hasOwn(FAILURE_COPY, code)) return code as AuthFailure;
  if (error.status === 429) return "rate_limited";
  return "unavailable";
}

/** An email-sending call's answer: `sent` (for any address: the facade is enumeration-safe), or why not. */
export function settled(answer: ApiResult<unknown>): "sent" | AuthFailure {
  return answer.ok ? "sent" : facadeFailure(answer.error);
}

const NOTICES: Readonly<Record<string, string>> = Object.freeze({
  link_expired: FAILURE_COPY.link_expired,
  link_invalid: "This link is not valid. Sign in, or request a new link.",
  session_ended: "Your session has ended. Sign in again.",
});

/** The login page's `?error=` notice: a known code's fixed copy, or nothing. */
export function loginNotice(code: string | null | undefined): string | null {
  return typeof code === "string" && Object.hasOwn(NOTICES, code) ? NOTICES[code] : null;
}

// -------------------------------------------------------------------------- the grant ---

export type OnboardingState =
  | { kind: "credited"; first: boolean; amount: Credit; grantedAt: string }
  | { kind: "unverified" }
  | { kind: "held"; reason: "identity_reused" | "rollout_hold" | "retired" }
  | { kind: "unavailable" }
  | { kind: "signed_out" };

/**
 * What one grant claim answer means. `granted` and `replayed` are the same credit (only the first
 * is announced as new); every denial is its own state; anything else — a failed call, the feature
 * off, an unknown status, an amount that is not an exact CREDIT decimal — is `unavailable`, so the
 * page offers a retry instead of guessing.
 */
export function claimOutcome(answer: ApiResult<S["GrantClaim"]>): OnboardingState {
  if (!answer.ok) return { kind: "unavailable" };
  const { status, credit, granted_at: grantedAt } = answer.data;
  switch (status) {
    case "granted":
    case "replayed": {
      try {
        if (credit?.unit !== "CREDIT" || typeof grantedAt !== "string") return { kind: "unavailable" };
        const amount = parseCredit(credit.amount);
        if (amount !== credit.amount) return { kind: "unavailable" };
        return { kind: "credited", first: status === "granted", amount, grantedAt };
      } catch {
        return { kind: "unavailable" };
      }
    }
    case "unverified":
      return { kind: "unverified" };
    case "identity_reused":
    case "rollout_hold":
    case "retired":
      return { kind: "held", reason: status };
    default:
      return { kind: "unavailable" };
  }
}

/**
 * The one grant claim, as the session `api` carries (A1's eligibility operation behind
 * `POST /console/v1/signup-grant/claim`, idempotent per individual, R71). Never throws.
 */
export async function claimGrant(api: ConsumerApi): Promise<OnboardingState> {
  // The transport answers every failure as a Result (never a throw): `claimOutcome` maps it.
  return claimOutcome(await api.call("post", "/console/v1/signup-grant/claim"));
}

/**
 * Where sign-in goes after claiming the grant once (the 02 first-login path; idempotent, R71). Only a
 * replayed grant — onboarding already complete — continues to `next`; a new grant, a hold, or a claim
 * that failed or threw lands on /welcome, which shows the balance or the retry. Never blocks sign-in.
 */
export async function afterSignIn(claim: () => Promise<OnboardingState>, next: string): Promise<string> {
  let state: OnboardingState | null = null;
  try {
    state = await claim();
  } catch {
    // the retry on /welcome
  }
  return state?.kind === "credited" && !state.first ? next : AFTER_VERIFY;
}

/** The onboarding action's decision: the session's user or nobody; a throwing port is retryable. */
export async function onboardingFor(
  sessionUserId: () => Promise<string | null>,
  claim: (userId: string) => Promise<OnboardingState>,
): Promise<OnboardingState> {
  try {
    const userId = await sessionUserId();
    if (!userId) return { kind: "signed_out" };
    return await claim(userId);
  } catch {
    return { kind: "unavailable" };
  }
}

// ------------------------------------------------------------------------- the wallet ---

export type WalletView =
  | { kind: "available"; available: Credit; grantedAt: string | null }
  | { kind: "not_issued" }
  | { kind: "unavailable" };

/**
 * `/console/v1/credits` for the onboarding page. No wallet (`wallet_id` null) is `not_issued` —
 * the grant has not landed — never a confirmed zero balance. A failed or inexact read is
 * `unavailable`.
 */
export function walletBalance(answer: ApiResult<S["Credits"]>): WalletView {
  if (!answer.ok) return { kind: "unavailable" };
  const { wallet_id: walletId, available: money, signup_granted_at: grantedAt } = answer.data;
  if (walletId === null) return { kind: "not_issued" };
  if (typeof walletId !== "string" || money?.unit !== "CREDIT") return { kind: "unavailable" };
  try {
    const available = parseCredit(money.amount);
    if (available !== money.amount) return { kind: "unavailable" };
    return { kind: "available", available, grantedAt: typeof grantedAt === "string" ? grantedAt : null };
  } catch {
    return { kind: "unavailable" };
  }
}

/** /welcome's read: `walletBalance` of one `GET /console/v1/credits` (a transport failure is a Result). */
export async function welcomeWallet(api: ConsumerApi): Promise<WalletView> {
  return walletBalance(await api.call("get", "/console/v1/credits"));
}

// ------------------------------------------------------------------------ the callback ---

export type CallbackPorts = {
  /** `GET /auth/v1/callback` with the link's parameters (and the PKCE verifier the App kept). */
  land(params: URLSearchParams): Promise<ApiResult<S["Landing"]>>;
  /** Keep the new session (the App's cookie). */
  store(session: S["Session"]): Promise<void>;
  /** The grant claim as that new session. */
  claim(session: S["Session"]): Promise<OnboardingState>;
};

export const EMAIL_LINK_TYPES = ["signup", "email", "magiclink", "recovery", "invite", "email_change"] as const;
export type EmailLinkType = (typeof EMAIL_LINK_TYPES)[number];

const LINK_EXPIRED = "/login?error=link_expired";
const LINK_INVALID = "/login?error=link_invalid";

/**
 * The email-link callback, as the path to redirect to. The facade verifies the link and reads only
 * a fixed `error_code`; the App keeps the session, claims the grant (idempotent, R71 - its failure
 * is the onboarding retry, not a failed sign-in) and lands on the facade's same-site path.
 */
export async function completeCallback(params: URLSearchParams, ports: CallbackPorts): Promise<string> {
  let answer: ApiResult<S["Landing"]>;
  try {
    answer = await ports.land(params);
  } catch {
    return LINK_INVALID;
  }
  if (!answer.ok) {
    const code = answer.error.kind === "error" ? answer.error.code : null;
    return code === "link_expired" ? LINK_EXPIRED : LINK_INVALID;
  }
  const { session, redirect } = answer.data;
  if (typeof session?.access_token !== "string" || session.access_token === "") return LINK_INVALID;
  try {
    await ports.store(session);
  } catch {
    return LINK_INVALID;
  }
  await ports.claim(session).catch(() => null);
  return safeNext(redirect, AFTER_VERIFY);
}

/** The `emailRedirectTo` for signup/resend links (the auth service's redirect allowlist, P-05). */
export const verifyRedirect = (origin: string) => `${origin}/auth/callback?next=${AFTER_VERIFY}`;
/** The recovery link: the PKCE form carries no `type`, so `next` is what reaches set-a-new-password. */
export const resetRedirect = (origin: string) => `${origin}/auth/callback?next=/update-password`;

// --------------------------------------------------------------------- the email forms ---

/** The PKCE challenge and the CAPTCHA token an email-sending form forwards (both optional). */
export type EmailExtras = { codeChallenge?: string | null; captchaToken?: string | null };

const extras = (e: EmailExtras) => ({
  ...(e.codeChallenge ? { code_challenge: e.codeChallenge } : {}),
  ...(e.captchaToken ? { captcha_token: e.captchaToken } : {}),
});

async function settle(call: () => Promise<ApiResult<unknown>>): Promise<"sent" | AuthFailure> {
  return settled(await call());
}

export function requestSignup(api: ConsumerApi, email: string, password: string, origin: string, more: EmailExtras = {}) {
  return settle(() => api.call("post", "/auth/v1/sign-up", { body: { email, password, redirect_to: verifyRedirect(origin), ...extras(more) } }));
}

export function requestReset(api: ConsumerApi, email: string, origin: string, more: EmailExtras = {}) {
  return settle(() => api.call("post", "/auth/v1/recovery", { body: { email, redirect_to: resetRedirect(origin), ...extras(more) } }));
}

/**
 * Resending the verification email has no facade route yet (wiring request WR-AP09-RESEND):
 * honest unavailable copy, never a "sent" that sent nothing.
 */
export const RESEND_UNAVAILABLE =
  "Resending the verification email is not available right now. Open the newest link we sent, or contact hello@callbill.ai.";

// ------------------------------------------------------------------------------- CAPTCHA ---

export type CaptchaGate = "off" | "unconfigured";

/**
 * LR-02, the form half: when the auth service requires a challenge (`captcha_required` from
 * `GET /auth/v1/availability`), a form without a configured challenge widget says so and sends
 * nothing; a token the widget leaves in the form (`captcha_token`) is forwarded as-is.
 * ponytail: no widget is pinned yet (coordinator wiring: the hosted provider's widget + its site
 * key in the P-05 checklist), so "required" is always "unconfigured" here; add "ready" with it.
 */
export function captchaGate(availability: ApiResult<S["AuthAvailability"]> | null): CaptchaGate {
  return availability?.ok === true && availability.data.captcha_required ? "unconfigured" : "off";
}

export const CAPTCHA_UNAVAILABLE =
  "This form needs a verification challenge that is not set up yet, so it cannot be sent right now. Try again later.";
