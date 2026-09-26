"use client";

import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useErrorReport } from "@/lib/deploy/error-view";

/**
 * The last resort. Everything this page *expects* to go wrong is a typed `Result` the view model
 * renders (an unreachable store, a stale cursor, a refused filter), so reaching this boundary means
 * something threw — and a thrown render leaves the reader with a half-streamed page and no way back.
 * A trace list is read-only, so there is nothing to reconcile: offer the retry and the clean URL.
 *
 * **`retry`, not `reset`.** This page is a Server Component, so its content comes from a server
 * payload that already errored: `reset()` re-renders the boundary's children *without re-fetching*
 * and therefore replays the same failure, which the U1 review reproduced in a real browser on the
 * installed Next 16.3.5. `retry()` re-fetches and re-renders, so it can actually recover once the
 * fault clears; the shipped docs
 * (`node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/error.md`, "In most
 * cases, you should use retry() instead", `retry` stable since v16.3.0) say the same.
 *
 * `error.message` is deliberately not shown: it is not copy written for a reader, and for a Server
 * Component error the client only receives a generic message plus a digest anyway.
 */
export default function TracesError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useErrorReport(error); // I3: reported like app/error.tsx, never shown
  return (
    <>
      <PageHeader title="Traces" />
      <Card>
        <CardContent role="alert" className="space-y-3 py-8 text-center">
          <p className="text-sm">The trace list could not be shown.</p>
          <div className="flex items-center justify-center gap-3">
            <Button size="sm" variant="outline" onClick={() => retry()}>
              Try again
            </Button>
            <Link href="/traces" className="text-sm text-primary underline-offset-4 hover:underline">
              Start from the first page
            </Link>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
