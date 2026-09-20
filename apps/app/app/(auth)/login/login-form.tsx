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
  const [pending, setPending] = useState<"email" | "google" | null>(null);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [googleNote, setGoogleNote] = useState(false);

  async function sendLink(e: React.FormEvent) {
    e.preventDefault();
    setPending("email");
    setError(null);
    const { error } = await createClient().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: callbackUrl(next) },
    });
    setPending(null);
    if (error) setError(error.message);
    else setSent(true);
  }

  async function signInWithGoogle() {
    setPending("google");
    setError(null);
    setGoogleNote(false);
    const { error } = await createClient().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: callbackUrl(next) },
    });
    if (error) {
      setPending(null);
      // Supabase answers "Unsupported provider: provider is not enabled" until the
      // Google client is configured on the project.
      if (/not enabled|unsupported provider/i.test(error.message)) setGoogleNote(true);
      else setError(error.message);
    }
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
        <Button type="submit" className="w-full" disabled={pending !== null}>
          {pending === "email" ? <Loader2 className="animate-spin" /> : null}
          Send magic link
        </Button>
      </form>

      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>

      <Button
        variant="outline"
        className="w-full"
        onClick={signInWithGoogle}
        disabled={pending !== null}
      >
        {pending === "google" ? <Loader2 className="animate-spin" /> : null}
        Continue with Google
      </Button>

      {googleNote ? (
        <p className="text-xs text-muted-foreground">
          Google sign-in is not enabled on this project yet. Use the magic link, or ask an operator
          to add a Google OAuth client in Supabase.
        </p>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
