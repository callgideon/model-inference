// UX-00 / UX-02: the console shell as production composes it (app/(console)/layout.tsx): the sidebar in a
// layout that persists across navigations, the page in <main id="console-main">. Synthetic account
// fixtures come from the query (fixture.tsx); no backend is read.
import { Suspense } from "react";
import { MAIN_ID } from "@/components/sidebar";
import { Fixture } from "./fixture";

export default function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <Suspense>
        <Fixture />
      </Suspense>
      <main id={MAIN_ID} tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 outline-none md:px-8 md:py-8">
        {children}
      </main>
    </div>
  );
}
