/**
 * A2: the decisions behind public signup, verification, sign-in and recovery, as pure functions.
 *
 * Nothing here imports Next or Supabase, so `node --test` runs it (tests/a). The route, the pages
 * and the server action are thin wiring around it:
 *
 * - `completeCallback` — what an email link does: verify, then claim the one-time grant for the
 *   user the AUTH SERVER verified (never an id from the URL), then land on onboarding. A claim that
 *   fails does not block sign-in; onboarding offers the retry.
 * - `claimOutcome` / `walletBalance` — what `claim_signup_grant` (A1, 0015) and
 *   `console_wallet_summary` (0008) mean for the page. An error or an inexact figure is
 *   `unavailable`, never a guessed credit or a confirmed zero.
 * - `authFailure` / `FAILURE_COPY` / `loginNotice` — fixed copy for every auth failure. The raw
 *   server text is never shown, and "no such account" reads exactly like "wrong password".
 */

import { parseCredit, type Credit } from "../../lib/contracts/v2/money-units.ts";

/** Where a verified email link lands by default: the onboarding page with the balance. */
export const AFTER_VERIFY = "/welcome";
/** Recorded as `signup_entitlements.campaign_version` (audit metadata only; R71). */
export const SIGNUP_CAMPAIGN = "consumer-v1";
/** Client-side hint only; the auth service's own policy is the authority (P-05). */
export const MIN_PASSWORD_LENGTH = 8;

/**
 * A same-site path to redirect to, or `fallback`. Stricter than `lib/utils` `safeNext`: a
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

export type AuthErrorLike = { code?: string | null; status?: number | null; name?: string | null; message?: string | null };

const BY_CODE: Readonly<Record<string, AuthFailure>> = Object.freeze({
  invalid_credentials: "invalid_credentials",
  user_not_found: "invalid_credentials",
  email_not_confirmed: "email_not_confirmed",
  over_request_rate_limit: "rate_limited",
  over_email_send_rate_limit: "rate_limited",
  weak_password: "weak_password",
  same_password: "same_password",
  email_address_invalid: "invalid_email",
  validation_failed: "invalid_email",
  signup_disabled: "signup_closed",
  email_provider_disabled: "signup_closed",
  email_address_not_authorized: "email_unavailable",
  otp_expired: "link_expired",
  flow_state_expired: "link_expired",
  flow_state_not_found: "link_expired",
  session_not_found: "link_expired",
  session_expired: "link_expired",
});

/** The fixed failure an auth error stands for. Unknown errors are `unavailable`, never their text. */
export function authFailure(error: AuthErrorLike): AuthFailure {
  if (error.code && Object.hasOwn(BY_CODE, error.code)) return BY_CODE[error.code];
  if (error.status === 429) return "rate_limited";
  if (error.name === "AuthSessionMissingError") return "link_expired";
  return "unavailable";
}

/**
 * The signup answer. An address that already has an account is `sent`, exactly like a new one: the
 * owner gets the usual email from the auth service, and nobody learns whether it was registered.
 */
export function signupSettled(error: AuthErrorLike | null | undefined): "sent" | AuthFailure {
  if (!error) return "sent";
  if (error.code === "user_already_exists" || error.code === "email_exists") return "sent";
  return authFailure(error);
}

/** The same for resend/reset: unknown addresses read as sent. */
export function emailSettled(error: AuthErrorLike | null | undefined): "sent" | AuthFailure {
  if (!error) return "sent";
  const failure = authFailure(error);
  return failure === "invalid_credentials" ? "sent" : failure;
}

const NOTICES: Readonly<Record<string, string>> = Object.freeze({
  link_expired: FAILURE_COPY.link_expired,
  link_invalid: "This link is not valid. Sign in, or request a new link.",
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

type PgFailure = { code?: string | null; message?: string | null } | null | undefined;

const one = (data: unknown): Record<string, unknown> | null => {
  if (Array.isArray(data)) return data.length === 1 && data[0] && typeof data[0] === "object" ? data[0] : null;
  return data && typeof data === "object" ? (data as Record<string, unknown>) : null;
};

/**
 * What one `claim_signup_grant` answer means. `granted` and `replayed` are the same credit (only the
 * first is announced as new); every denial is its own state; anything else — an error, the feature
 * off (55000), no row, several rows, an unknown status, an amount that is not an exact decimal
 * string — is `unavailable`, so the page offers a retry instead of guessing.
 */
export function claimOutcome(data: unknown, error: PgFailure): OnboardingState {
  if (error) return { kind: "unavailable" };
  const row = one(data);
  if (row === null) return { kind: "unavailable" };
  switch (row.status) {
    case "granted":
    case "replayed": {
      try {
        const amount = parseCredit(row.amount);
        if (amount !== row.amount || typeof row.granted_at !== "string") return { kind: "unavailable" };
        return { kind: "credited", first: row.status === "granted", amount, grantedAt: row.granted_at };
      } catch {
        return { kind: "unavailable" };
      }
    }
    case "unverified":
      return { kind: "unverified" };
    case "identity_reused":
    case "rollout_hold":
    case "retired":
      return { kind: "held", reason: row.status };
    default:
      return { kind: "unavailable" };
  }
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
 * `console_wallet_summary(user)` for the onboarding page. No wallet (`wallet_id` null) is
 * `not_issued` — the grant has not landed — never a confirmed zero balance. A failed or inexact read
 * is `unavailable`.
 */
export function walletBalance(data: unknown, error: PgFailure): WalletView {
  if (error) return { kind: "unavailable" };
  const row = one(data);
  if (row === null || row.unit !== "CREDIT") return { kind: "unavailable" };
  if (row.wallet_id === null) return { kind: "not_issued" };
  if (typeof row.wallet_id !== "string") return { kind: "unavailable" };
  try {
    const available = parseCredit(row.available);
    if (available !== row.available) return { kind: "unavailable" };
    return {
      kind: "available",
      available,
      grantedAt: typeof row.signup_granted_at === "string" ? row.signup_granted_at : null,
    };
  } catch {
    return { kind: "unavailable" };
  }
}

// ------------------------------------------------------------------------ the callback ---

type Outcome = { error: AuthErrorLike | null };

export type CallbackPorts = {
  exchangeCode(code: string): Promise<Outcome>;
  verifyOtp(tokenHash: string, type: EmailLinkType): Promise<Outcome>;
  /** The user the auth server verified for the new session (`getUser()`), or null. */
  verifiedUserId(): Promise<string | null>;
  claim(userId: string): Promise<OnboardingState>;
};

export const EMAIL_LINK_TYPES = ["signup", "email", "magiclink", "recovery", "invite", "email_change"] as const;
export type EmailLinkType = (typeof EMAIL_LINK_TYPES)[number];

const LINK_EXPIRED = "/login?error=link_expired";
const LINK_INVALID = "/login?error=link_invalid";

/**
 * The email-link callback, as the path to redirect to. The provider's `error_description` is free
 * text anyone can put in a link, so only the `error_code` is read, and only as a fixed code.
 */
export async function completeCallback(params: URLSearchParams, ports: CallbackPorts): Promise<string> {
  if (params.get("error") || params.get("error_code")) {
    return authFailure({ code: params.get("error_code") }) === "link_expired" ? LINK_EXPIRED : LINK_INVALID;
  }
  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type");
  const isType = (value: string | null): value is EmailLinkType =>
    value !== null && (EMAIL_LINK_TYPES as readonly string[]).includes(value);

  let outcome: Outcome;
  try {
    if (code) outcome = await ports.exchangeCode(code);
    else if (tokenHash && isType(type)) outcome = await ports.verifyOtp(tokenHash, type);
    else return LINK_INVALID;
  } catch {
    return LINK_INVALID;
  }
  if (outcome.error) return authFailure(outcome.error) === "link_expired" ? LINK_EXPIRED : LINK_INVALID;

  const userId = await ports.verifiedUserId().catch(() => null);
  if (!userId) return LINK_INVALID;
  // Idempotent (R71): every callback, sign-in and retry resolves to the one grant. Its failure is
  // the onboarding page's retry, not a failed sign-in.
  await ports.claim(userId).catch(() => null);

  return type === "recovery" ? "/update-password" : safeNext(params.get("next"), AFTER_VERIFY);
}

/** The `emailRedirectTo` for signup/resend links (the auth service's redirect allowlist, P-05). */
export const verifyRedirect = (origin: string) => `${origin}/auth/callback?next=${AFTER_VERIFY}`;
