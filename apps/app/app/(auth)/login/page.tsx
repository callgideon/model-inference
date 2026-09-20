import Link from "next/link";
import { safeNext } from "@/lib/utils";
import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in · infrx" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const next = safeNext(typeof params.next === "string" ? params.next : null);
  const error = typeof params.error === "string" ? params.error : null;

  return (
    <>
      <div className="space-y-2 text-center">
        <div className="font-heading text-2xl font-semibold tracking-tight">infrx</div>
        <p className="text-sm text-muted-foreground">
          Sign in to manage API keys and usage for the infrx API
        </p>
      </div>

      <LoginForm next={next} initialError={error} />

      <div className="space-y-2 text-center text-xs text-muted-foreground">
        <p>
          <Link
            href="/forgot-password"
            className="underline underline-offset-4 hover:text-foreground"
          >
            Forgot password?
          </Link>
        </p>
        <p>
          Accounts are created by invitation;{" "}
          <a
            href="mailto:hello@callbill.ai"
            className="underline underline-offset-4 hover:text-foreground"
          >
            contact us
          </a>
          .
        </p>
      </div>
    </>
  );
}
