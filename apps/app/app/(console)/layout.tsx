import { Sidebar } from "@/components/sidebar";
import { getSession } from "@/lib/session";
import { getBalance } from "@/lib/credits";
import { displayMoney } from "@/lib/contracts/money";

// Every console page reads Supabase with the user's cookie: never prerender.
export const dynamic = "force-dynamic";

export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
  const session = await getSession();
  const balance = await getBalance(session.orgId);

  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <Sidebar email={session.email} balance={displayMoney(balance)} isOperator={session.isOperator} />
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
