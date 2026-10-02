"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resendVerification } from "../auth-actions";
import { CAPTCHA_UNAVAILABLE, FAILURE_COPY } from "../flow";

/** The auth service refuses a second verification email to one address within about a minute. */
const COOLDOWN_MS = 60_000;

/**
 * Resend the verification email (`POST /auth/v1/resend` via the server action). The answer is the
 * same whether or not the address has an account waiting for verification; only rate limits,
 * outages and an unconfigured challenge are reported, as fixed copy.
 */
export function ResendForm({ email }: { email?: string }) {
  const [pending, setPending] = useState(false);
  const [coolingDown, setCoolingDown] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (email) form.set("email", email);
    setPending(true);
    setError(null);
    setNotice(null);
    const settled = await resendVerification(form).catch(() => "unavailable" as const);
    setPending(false);
    if (settled === "sent") {
      setNotice("If that address is waiting for verification, a new link is on its way.");
      setCoolingDown(true);
      setTimeout(() => setCoolingDown(false), COOLDOWN_MS);
    } else {
      setError(settled === "captcha_unconfigured" ? CAPTCHA_UNAVAILABLE : FAILURE_COPY[settled]);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {email ? null : (
        <div className="space-y-2">
          <Label htmlFor="resend-email">Email</Label>
          <Input id="resend-email" name="email" type="email" autoComplete="email" required />
        </div>
      )}
      <Button type="submit" variant="outline" className="w-full" disabled={pending || coolingDown}>
        {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
        Resend verification email
      </Button>
      <p className="text-center text-xs text-muted-foreground" aria-live="polite">
        {notice}
      </p>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  );
}
