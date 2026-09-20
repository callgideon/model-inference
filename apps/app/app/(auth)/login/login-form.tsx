"use client";

import { useState } from "react";
import { Loader2, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";

function callbackUrl(next: string) {
  const origin = process.env.NEXT_PUBLIC_APP_URL || window.location.origin;
  return `${origin}/auth/callback?next=${encodeURIComponent(next)}`;
}

export function LoginForm({ next, initialError }: { next: string; initialError: string | null }) {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  async function sendLink(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const { error } = await createClient().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: callbackUrl(next) },
    });
    setPending(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  if (sent) {
    return (
      <div className="rounded-xl border bg-card p-6 text-center text-sm">
        <Mail className="mx-auto mb-3 size-5 text-muted-foreground" />
        <p className="font-medium">Check your inbox</p>
        <p className="mt-1 text-muted-foreground">
          We sent a sign-in link to <span className="text-foreground">{email}</span>.
        </p>
        <Button variant="ghost" size="sm" className="mt-4" onClick={() => setSent(false)}>
          Use a different email
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-xl border bg-card p-6">
      <form onSubmit={sendLink} className="space-y-3">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full"
        />
        <Button type="submit" className="w-full" disabled={pending}>
          {pending ? <Loader2 className="animate-spin" /> : null}
          Send magic link
        </Button>
      </form>

      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
