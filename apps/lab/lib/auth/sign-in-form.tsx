"use client";
// WR-L1-6: the Lab sign-in form (the provider layout's signed-out card). Password only: consumer
// signup and password recovery stay in the App, so no recovery link is guessed here (L-01).
// UX-03: labels above the fields, "Signing in…" while pending (no double submit); a failure shows
// fixed copy only and the password field is never refilled.
import { useActionState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { SIGN_IN_COPY } from "./access.ts";
import { signIn } from "./sign-in.ts";

export function SignInForm() {
  const [state, action, pending] = useActionState(signIn, null);
  return (
    <form action={action} className="lab-stack">
      <Field label="Email">
        <Input name="email" type="email" autoComplete="username" required />
      </Field>
      <Field label="Password">
        <Input name="password" type="password" autoComplete="current-password" required />
      </Field>
      {state && <p role="alert">{SIGN_IN_COPY[state.error]}</p>}
      <Button type="submit" variant="primary" pending={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
