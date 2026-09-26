"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { FAILURE_COPY, MIN_PASSWORD_LENGTH, requestSignup } from "../flow";
import { ResendForm } from "../verify-email/resend-form";

/**
 * Email/password signup against the configured auth service. Every address gets the same "check
 * your email" answer — including one that already has an account — so the form cannot be used to
 * learn who is registered. The grant happens after verification, on the server (the callback).
 */
export function SignupForm() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (sentTo) heading.current?.focus();
  }, [sentTo]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email")).trim();
    setPending(true);
    setError(null);
    const settled = await requestSignup(createClient().auth, email, String(form.get("password")), window.location.origin);
    setPending(false);
    if (settled === "sent") setSentTo(email);
    else setError(FAILURE_COPY[settled]);
  }

  if (sentTo) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border bg-muted/40 p-3 text-center text-sm">
          <h2 ref={heading} tabIndex={-1} className="font-medium outline-none">
            Check your email
          </h2>
          <p className="mt-1 text-muted-foreground">
            We sent a verification link to {sentTo}. Open it on this device to finish creating your account.
          </p>
        </div>
        <ResendForm email={sentTo} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" autoComplete="email" required autoFocus />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          minLength={MIN_PASSWORD_LENGTH}
          aria-describedby="password-hint"
          required
        />
        <p id="password-hint" className="text-xs text-muted-foreground">
          At least {MIN_PASSWORD_LENGTH} characters.
        </p>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
        Create account
      </Button>
    </form>
  );
}
