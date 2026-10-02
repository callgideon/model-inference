import { Skeleton } from "@/components/ui/skeleton";

/** Next's own loading state for this route, in the page's order: account, privacy facts, data use. */
export default function SettingsLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading settings…</span>
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-6 h-32" />
      <Skeleton className="mt-6 h-64" />
      <Skeleton className="mt-6 h-40" />
    </div>
  );
}
