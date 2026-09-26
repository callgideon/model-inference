"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";
import { FAILURE_COPY, MIN_PASSWORD_LENGTH as MIN_LENGTH, authFailure, type AuthFailure } from "../flow";

export default function UpdatePasswordPage() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password"));
    if (password !== String(form.get("confirm"))) {
      setError("The two passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);

    const { error } = await createClient()
      .auth.updateUser({ password })
      .catch(() => ({ error: { status: 0 } }));
    if (error) {
      const failure: AuthFailure = authFailure(error);
      setExpired(failure === "link_expired");
      setError(FAILURE_COPY[failure]);
      setPending(false);
      return;
    }

    toast.success("Password updated");
    router.push("/models");
    router.refresh();
  }

  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Set a new password</h1>
        <p className="text-sm text-muted-foreground">At least {MIN_LENGTH} characters.</p>
      </div>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="password">New password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={MIN_LENGTH}
            required
            autoFocus
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="confirm">Confirm password</Label>
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={MIN_LENGTH}
            required
          />
        </div>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}{" "}
            {expired ? (
              <Link href="/forgot-password" className="underline underline-offset-4">
                Request a new reset link
              </Link>
            ) : null}
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={pending}>
          {pending ? <Loader2 className="animate-spin" aria-hidden /> : null}
          Update password
        </Button>
      </form>
    </>
  );
}
