import { Skeleton } from "@/components/ui/skeleton";

/** Next's own loading state for this route: the page is dynamic, so navigation shows this first. */
export default function UsageLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading usage…</span>
      <Skeleton className="h-8 w-40" />
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[0, 1, 2, 3, 4].map((tile) => (
          <Skeleton key={tile} className="h-20" />
        ))}
      </div>
      <Skeleton className="mt-6 h-56" />
      <Skeleton className="mt-6 h-72" />
    </div>
  );
}
