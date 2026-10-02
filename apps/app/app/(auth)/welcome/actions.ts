"use server";

import { accessToken, apiSource } from "@/lib/request-api";
import { claimGrant, onboardingFor, type OnboardingState } from "../flow";

/**
 * Claim the signup grant for the signed-in user: from the onboarding page's retry. No arguments —
 * the individual is the session the API verifies, never one a caller names. Next checks a server
 * action's Origin against its host, so a cross-site form cannot trigger it.
 */
export async function claimOnboarding(): Promise<OnboardingState> {
  return onboardingFor(accessToken, async () => claimGrant((await apiSource()).api));
}
