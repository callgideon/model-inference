import { redirect } from "next/navigation";
import { ConsoleDataUnavailable } from "@/components/console-data-state";
import { Sidebar } from "@/components/sidebar";
import { consoleShell } from "@/lib/services/console";
import { consumerSession } from "@/lib/services/server";
import { getSession } from "@/lib/session"; // the operator flag only (WR-6)
import { consumerCreditReads } from "./billing/credit-context";
import { sidebarCredits } from "./billing/credit-view-model";

// Every console page reads Supabase with the user's cookie: never prerender.
export const dynamic = "force-dynamic";

// Set each to its path in the commit that ships the route (A2: /verify-email; C3A/A2: /onboarding).
// Until then the state gets a fixed panel: a redirect to a missing route is a 404.
const ROUTES = { verifyEmail: null, onboarding: null };

export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
  const session = await consumerSession();
  const { state } = session.context;
  // Only a signed-in, verified user has an operator flag worth reading. An operator with no consumer
  // wallet still gets the shell, so /admin (which checks the role itself) stays reachable.
  const isOperator = state === "ready" || state === "onboarding" ? (await getSession()).isOperator : false;
  const shell = consoleShell(session, isOperator, ROUTES);
  if (shell.kind === "redirect") redirect(shell.to);
  if (shell.kind === "panel") {
    return (
      <main className="px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">
          <ConsoleDataUnavailable title="Your account" />
        </div>
      </main>
    );
  }
  // The individual's CREDIT wallet (U1R WR-2): exact available credits, "No credits yet", or `null`
  // when the wallet could not be read or an operator has none - fixed copy, never a zero, never the
  // org's legacy USD figure.
  const balance = shell.reads === null ? null : sidebarCredits(await (await consumerCreditReads()).reads.wallet());

  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <Sidebar email={shell.email} balance={balance} isOperator={isOperator} />
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
