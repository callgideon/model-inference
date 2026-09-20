import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in · infrx" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const next = typeof params.next === "string" ? params.next : "/models";
  const error = typeof params.error === "string" ? params.error : null;

  return (
    <main className="flex min-h-svh items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-2 text-center">
          <div className="font-heading text-2xl font-semibold tracking-tight">infrx</div>
          <p className="text-sm text-muted-foreground">
            Sign in to manage keys, usage and credits.
          </p>
        </div>
        <LoginForm next={next} initialError={error} />
        <p className="text-center text-xs text-muted-foreground">
          By signing in you agree to be billed for what you call.
        </p>
      </div>
    </main>
  );
}
