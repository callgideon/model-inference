import Link from "next/link";
import { ResendForm } from "./resend-form";

export const metadata = { title: "Verify your email · infrx" };

export default function VerifyEmailPage() {
  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Verify your email</h1>
        <p className="text-sm text-muted-foreground">
          Open the link in the email we sent you. Your 10,000 signup credits are issued once your address is
          verified. Links expire; request a new one below if yours did.
        </p>
      </div>
      <ResendForm />
      <p className="text-center text-xs text-muted-foreground">
        <Link href="/login" className="underline underline-offset-4 hover:text-foreground">
          Back to sign in
        </Link>
      </p>
    </>
  );
}
