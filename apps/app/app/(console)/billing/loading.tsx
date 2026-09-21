import { Skeleton } from "@/components/ui/skeleton";

/** Next's own loading state for this route: the page is dynamic, so navigation shows this first. */
export default function BillingLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading balance…</span>
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-6 h-44" />
      <Skeleton className="mt-4 h-72" />
    </div>
  );
}
