import { createAdminClient } from "@/lib/supabase/admin";
import { CLAIM_RPC, claimArgs, claimOutcome, type OnboardingState } from "./flow";

/** Server only: the service-role client must never reach a browser bundle (tests/a A2-BUNDLE-*). */
if (typeof window !== "undefined") throw new Error("app/(auth)/grant.ts is server-only");

/**
 * A1's one eligibility operation (`public.claim_signup_grant`, 0015), for a user id the caller took
 * from the verified session. The database derives verification itself and is idempotent per
 * individual (R71), so the callback, sign-in and the onboarding retry may all call this; none can
 * mint a second grant. Service role only: `authenticated` has no execute on it.
 */
export async function claimSignupGrant(userId: string): Promise<OnboardingState> {
  try {
    const { data, error } = await createAdminClient().rpc(CLAIM_RPC, claimArgs(userId));
    const state = claimOutcome(data, error);
    // Safe identifiers only: the outcome and the SQLSTATE, never the address or the message.
    if (state.kind === "unavailable") console.warn(`signup grant unavailable (code ${error?.code ?? "none"})`);
    return state;
  } catch {
    console.warn("signup grant unavailable (no answer)");
    return { kind: "unavailable" };
  }
}
