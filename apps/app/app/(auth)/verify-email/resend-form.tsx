"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { FAILURE_COPY, requestResend } from "../flow";

/** The auth service refuses a second verification email to one address within about a minute. */
const COOLDOWN_MS = 60_000;

/**
 * Resend the verification email. The answer is the same whether or not the address has an account
 * waiting for verification; only rate limits and outages are reported, as fixed copy.
 */
export function ResendForm({ email }: { email?: string }) {
  const [pending, setPending] = useState(false);
  const [coolingDown, setCoolingDown] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const address = email ?? String(new FormData(event.currentTarget).get("email"));
    setPending(true);
    setError(null);
    setNotice(null);
    const settled = await requestResend(createClient().auth, address, window.location.origin);
    setPending(false);
    if (settled === "sent") {
      setNotice("If that address is waiting for verification, a new link is on its way.");
      setCoolingDown(true);
      setTimeout(() => setCoolingDown(false), COOLDOWN_MS);
    } else {
      setError(FAILURE_COPY[settled]);
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
