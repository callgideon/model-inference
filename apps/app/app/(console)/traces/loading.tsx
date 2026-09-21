import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** The loading state of the list: the real one, shown by the router while the page streams. */
export default function TracesLoading() {
  return (
    <>
      <PageHeader title="Traces" subtitle="Loading traces…" />
      <Card>
        <CardContent className="space-y-2 py-4" aria-busy="true" aria-live="polite">
          <span className="sr-only">Loading traces</span>
          {Array.from({ length: 8 }, (_unused, index) => (
            <Skeleton key={index} className="h-6 w-full" />
          ))}
        </CardContent>
      </Card>
    </>
  );
}
