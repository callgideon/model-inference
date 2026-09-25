"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { FAILURE_COPY, emailSettled } from "../flow";

export default function ForgotPasswordPage() {
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email"));
    setPending(true);
    setError(null);
    const { error } = await createClient()
      .auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback?next=/update-password`,
      })
      .catch(() => ({ error: { status: 0 } }));
    setPending(false);
    // The same message whether or not the address has an account; only a rate limit or an
    // outage is reported, because "sent" would then be untrue.
    const settled = emailSettled(error);
    if (settled === "sent") setSent(true);
    else setError(FAILURE_COPY[settled]);
  }

  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Reset password</h1>
        <p className="text-sm text-muted-foreground">
          We will email you a link to set a new password.
        </p>
      </div>

      {sent ? (
        <p role="status" className="rounded-lg border bg-muted/40 p-3 text-center text-sm">
          If that address has an account, a reset link is on its way. It expires; request another if it
          does.
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" name="email" type="email" autoComplete="email" required autoFocus />
          </div>
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Send reset link
          </Button>
        </form>
      )}

      <p className="text-center text-xs text-muted-foreground">
        <Link href="/login" className="underline underline-offset-4 hover:text-foreground">
          Back to sign in
        </Link>
      </p>
    </>
  );
}
