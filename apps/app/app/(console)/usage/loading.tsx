import { Skeleton } from "@/components/ui/skeleton";

/** Next's own loading state for this route, in the page's order: requests, then the credit summary. */
export default function UsageLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading usage…</span>
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-6 h-72" />
      <Skeleton className="mt-6 h-20" />
    </div>
  );
}
