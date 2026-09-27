"use client";
// WR-L1-6: the Lab sign-in form (the provider layout's signed-out state). Password only: consumer
// signup and password recovery stay in the App.
import { useActionState } from "react";
import { SIGN_IN_COPY } from "./access.ts";
import { signIn } from "./sign-in.ts";

export function SignInForm() {
  const [state, action, pending] = useActionState(signIn, null);
  return (
    <form action={action}>
      <label>
        Email <input name="email" type="email" autoComplete="email" required />
      </label>
      <label>
        Password <input name="password" type="password" autoComplete="current-password" required />
      </label>
      {state && <p role="alert">{SIGN_IN_COPY[state.error]}</p>}
      <button type="submit" disabled={pending}>
        Sign in
      </button>
    </form>
  );
}
