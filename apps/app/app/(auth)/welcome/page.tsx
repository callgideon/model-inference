import Link from "next/link";
import { redirect } from "next/navigation";
import { KeyRound, BookOpen, Boxes } from "lucide-react";
import { displayCredit } from "@/lib/contracts/v2/money-units";
import { createClient } from "@/lib/supabase/server";
import { welcomeWallet } from "../flow";
import { RetryGrant } from "./retry";

export const metadata = { title: "Welcome · infrx" };
export const dynamic = "force-dynamic";

const NEXT_STEPS = [
  { href: "/api-keys", icon: KeyRound, title: "Create an API key", body: "Shown once; store it somewhere safe." },
  { href: "/docs", icon: BookOpen, title: "Send your first request", body: "Copy a working finite-video example." },
  { href: "/models", icon: Boxes, title: "See the models", body: "Inputs, limits and the CREDIT rate." },
];

/** After verification: the wallet's actual balance and the next step, or a recoverable state. */
export default async function WelcomePage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login?next=/welcome");

  const wallet = await welcomeWallet(() => supabase.rpc("console_wallet_summary", { p_user: user.id }));

  return (
    <>
      <div className="space-y-2 text-center">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Welcome to infrx</h1>
        <p className="text-sm text-muted-foreground">Signed in as {user.email}</p>
      </div>

      <section aria-labelledby="balance-heading" className="rounded-lg border p-4 text-center">
        <h2 id="balance-heading" className="text-sm font-medium text-muted-foreground">
          Available balance
        </h2>
        {wallet.kind === "available" ? (
          <>
            <p className="mt-1 font-heading text-2xl font-semibold tabular-nums">
              {displayCredit(wallet.available)}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Your one-time 10,000 CREDIT signup promotion. It is not refilled; requests draw it down.
            </p>
          </>
        ) : wallet.kind === "not_issued" ? (
          <>
            <p className="mt-1 text-sm">Your signup credits have not been issued yet.</p>
            <RetryGrant />
          </>
        ) : (
          <p className="mt-1 text-sm" role="status">
            Your balance could not be loaded right now.{" "}
            <Link href="/welcome" className="underline underline-offset-4">
              Reload
            </Link>
          </p>
        )}
      </section>

      <nav aria-label="Next steps">
        <ul className="space-y-2">
          {NEXT_STEPS.map(({ href, icon: Icon, title, body }) => (
            <li key={href}>
              <Link
                href={href}
                className="flex items-start gap-3 rounded-lg border p-3 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span>
                  <span className="block font-medium">{title}</span>
                  <span className="block text-muted-foreground">{body}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </>
  );
}
