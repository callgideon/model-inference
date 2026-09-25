"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { FAILURE_COPY, afterSignIn, authFailure, type AuthFailure } from "../flow";
import { claimOnboarding } from "../welcome/actions";

export function LoginForm({ next, initialError }: { next: string; initialError: string | null }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<AuthFailure | null>(null);
  const [notice, setNotice] = useState<string | null>(initialError);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setFailure(null);
    setNotice(null);

    const { error } = await createClient()
      .auth.signInWithPassword({
        email: String(form.get("email")),
        password: String(form.get("password")),
      })
      .catch(() => ({ error: { status: 0 } }));
    if (error) {
      setFailure(authFailure(error));
      setPending(false);
      return;
    }

    // The first-login path (02): an existing verified user, or one whose link was opened on another
    // device, receives the one-time grant here; anything short of a replayed grant lands on /welcome.
    // `next` is already checked to be a same-site path by the page.
    router.push(await afterSignIn(claimOnboarding, next));
    router.refresh();
  }

  const message = failure ? FAILURE_COPY[failure] : notice;
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
          autoComplete="current-password"
          required
        />
      </div>
      {message ? (
        <p role="alert" className="text-xs text-destructive">
          {message}{" "}
          {failure === "email_not_confirmed" ? (
            <Link href="/verify-email" className="underline underline-offset-4">
              Resend the link
            </Link>
          ) : null}
        </p>
      ) : null}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
        Sign in
      </Button>
    </form>
  );
}
