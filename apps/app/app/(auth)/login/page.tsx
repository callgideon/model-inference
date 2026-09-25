import Link from "next/link";
import { loginNotice, safeNext } from "../flow";
import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in · infrx" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const next = safeNext(typeof params.next === "string" ? params.next : null);
  // Only a known code's fixed copy: `?error=` is anyone's to write, so its text is never shown.
  const notice = loginNotice(typeof params.error === "string" ? params.error : null);

  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">infrx</h1>
        <p className="text-sm text-muted-foreground">
          Sign in to manage API keys and usage for the infrx API
        </p>
      </div>

      <LoginForm next={next} initialError={notice} />

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
          New to infrx?{" "}
          <Link href="/signup" className="underline underline-offset-4 hover:text-foreground">
            Create an account
          </Link>
        </p>
      </div>
    </>
  );
}
