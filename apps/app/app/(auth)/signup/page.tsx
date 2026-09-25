import Link from "next/link";
import { SignupForm } from "./signup-form";

export const metadata = { title: "Create an account · infrx" };

export default function SignupPage() {
  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Create an infrx account</h1>
        <p className="text-sm text-muted-foreground">
          Verify your email to receive a one-time 10,000 CREDIT promotion for the infrx API. It is issued once
          per person and is not refilled.
        </p>
      </div>
      <SignupForm />
      <p className="text-center text-xs text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="underline underline-offset-4 hover:text-foreground">
          Sign in
        </Link>
      </p>
    </>
  );
}
