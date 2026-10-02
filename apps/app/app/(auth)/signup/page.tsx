import Link from "next/link";
import { consumerApi } from "@/lib/api/index";
import { apiBaseUrl } from "../../(console)/models/catalog";
import { captchaGate } from "../flow";
import { SignupForm } from "./signup-form";

export const metadata = { title: "Create an account · infrx" };

export const dynamic = "force-dynamic";

export default async function SignupPage() {
  // LR-02: the auth service's own CAPTCHA requirement (a public read, no session).
  const facade = consumerApi({ baseUrl: apiBaseUrl(process.env) ?? "" });
  const gate = captchaGate(await facade.call("get", "/auth/v1/availability").catch(() => null));
  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Create an infrx account</h1>
        <p className="text-sm text-muted-foreground">
          Verify your email to receive a one-time 10,000 CREDIT promotion for the infrx API. It is issued once
          per person and is not refilled.
        </p>
      </div>
      <SignupForm gate={gate} />
      <p className="text-center text-xs text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="underline underline-offset-4 hover:text-foreground">
          Sign in
        </Link>
      </p>
    </>
  );
}
