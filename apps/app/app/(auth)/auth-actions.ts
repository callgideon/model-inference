"use server";

/**
 * The auth forms' server actions (AP-09 09b): each is one `/auth/v1/*` facade call, then the App's
 * cookie. Next checks a server action's Origin against its host, so a cross-site form cannot run
 * these; the facade checks again. Every failure is a fixed `AuthFailure` (copy in `./flow.ts`).
 */
import { accessToken } from "@/lib/request-api";
import {
  afterSignIn,
  captchaGate,
  claimGrant,
  facadeFailure,
  requestReset,
  requestSignup,
  safeNext,
  type AuthFailure,
} from "./flow";
import { facadeApi, newChallenge, origin, sessionApi, storeSession } from "./session";

/** A challenge widget's token, if the form carries one (LR-02; forwarded as-is to the facade). */
const captchaOf = (form: FormData) => {
  const token = form.get("captcha_token");
  return typeof token === "string" && token !== "" ? token : null;
};

export async function signIn(email: string, password: string, next: string): Promise<{ to: string } | { failure: AuthFailure }> {
  try {
    const answer = await facadeApi().call("post", "/auth/v1/sign-in", { body: { email, password } });
    if (!answer.ok) return { failure: facadeFailure(answer.error) };
    await storeSession(answer.data);
    // The first-login path (02): an existing verified user, or one whose link was opened on another
    // device, receives the one-time grant here; anything short of a replayed grant lands on /welcome.
    return { to: await afterSignIn(() => claimGrant(sessionApi(answer.data)), safeNext(next)) };
  } catch {
    return { failure: "unavailable" };
  }
}

/** LR-02: whether an email form may be sent at all (the auth service's CAPTCHA requirement). */
export async function emailFormGate() {
  return captchaGate(await facadeApi().call("get", "/auth/v1/availability").catch(() => null));
}

export async function signUp(form: FormData): Promise<"sent" | AuthFailure | "captcha_unconfigured"> {
  if (captchaOf(form) === null && (await emailFormGate()) === "unconfigured") return "captcha_unconfigured";
  const email = String(form.get("email") ?? "").trim();
  return requestSignup(facadeApi(), email, String(form.get("password") ?? ""), await origin(), {
    codeChallenge: await newChallenge(),
    captchaToken: captchaOf(form),
  });
}

export async function recover(form: FormData): Promise<"sent" | AuthFailure | "captcha_unconfigured"> {
  if (captchaOf(form) === null && (await emailFormGate()) === "unconfigured") return "captcha_unconfigured";
  return requestReset(facadeApi(), String(form.get("email") ?? "").trim(), await origin(), {
    codeChallenge: await newChallenge(),
    captchaToken: captchaOf(form),
  });
}

/** A new password for the signed-in (or recovery-link) session; no session is the expired-link state. */
export async function updatePassword(password: string): Promise<"updated" | AuthFailure> {
  const token = await accessToken();
  if (token === null) return "link_expired";
  try {
    const answer = await facadeApi(token).call("post", "/auth/v1/password", { body: { password } });
    return answer.ok ? "updated" : facadeFailure(answer.error);
  } catch {
    return "unavailable";
  }
}
