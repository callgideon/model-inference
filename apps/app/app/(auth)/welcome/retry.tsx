"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { OnboardingState } from "../flow";
import { claimOnboarding } from "./actions";

const COPY: Record<Exclude<OnboardingState["kind"], "credited">, string> = {
  unverified: "Your email address is not verified yet. Open the link in your verification email first.",
  held: "Your credits need a manual check by our team before they can be issued. Contact hello@callbill.ai.",
  unavailable: "We could not issue your credits right now. Nothing was charged; try again in a moment.",
  signed_out: "Your session has ended. Sign in again to continue.",
};

/** The recoverable half of onboarding: ask the server to (re)claim the one-time grant. */
export function RetryGrant() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function retry() {
    setPending(true);
    setMessage(null);
    const state = await claimOnboarding().catch((): OnboardingState => ({ kind: "unavailable" }));
    setPending(false);
    if (state.kind === "credited") {
      router.refresh();
      return;
    }
    setMessage(COPY[state.kind]);
  }

  return (
    <div className="mt-3 space-y-2">
      <Button onClick={retry} disabled={pending}>
        {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
        Issue my credits
      </Button>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {message}
      </p>
    </div>
  );
}
