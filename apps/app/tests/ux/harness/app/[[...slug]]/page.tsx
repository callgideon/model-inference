// UX-00 / UX-02: the console shell at any path, with synthetic account fixtures from the query:
// ?balance=unavailable (the wallet could not be read), ?operator=1, ?email=long. No backend is read.
import { Sidebar } from "@/components/sidebar";

const LONG_EMAIL = "a-very-long-synthetic-address-for-overflow-checks.with.many.parts@subdomain.example.test";

export default async function Shell({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const q = await searchParams;
  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <Sidebar
        email={q.email === "long" ? LONG_EMAIL : "fixture@example.test"}
        balance={q.balance === "unavailable" ? null : "10,000"}
        isOperator={q.operator === "1"}
      />
      <main id="console-main" tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <h1>Synthetic page</h1>
        <button type="button">Page control</button>
      </main>
    </div>
  );
}
