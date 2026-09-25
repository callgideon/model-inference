"use server";

import { createClient } from "@/lib/supabase/server";
import { onboardingFor, type OnboardingState } from "../flow";
import { claimSignupGrant } from "../grant";

/**
 * Claim the signup grant for the signed-in user: after sign-in (the first-login path for existing
 * verified users) and from the onboarding page's retry. No arguments — the user is the session's
 * `getUser()` (revalidated by the auth server), never one a caller names. Next checks a server
 * action's Origin against its host, so a cross-site form cannot trigger it.
 */
export async function claimOnboarding(): Promise<OnboardingState> {
  const supabase = await createClient();
  return onboardingFor(async () => (await supabase.auth.getUser()).data.user?.id ?? null, claimSignupGrant);
}
