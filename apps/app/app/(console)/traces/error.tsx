"use client";

import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The last resort. Everything this page *expects* to go wrong is a typed `Result` the view model
 * renders (an unreachable store, a stale cursor, a refused filter), so reaching this boundary means
 * something threw — and a thrown render leaves the reader with a half-streamed page and no way back.
 * A trace list is read-only, so there is nothing to reconcile: offer the retry and the clean URL.
 *
 * `error.message` is deliberately not shown: it is not a message written for a reader, and on the
 * server it is redacted to a digest anyway.
 */
export default function TracesError({ reset }: { error: Error; reset: () => void }) {
  return (
    <>
      <PageHeader title="Traces" />
      <Card>
        <CardContent role="alert" className="space-y-3 py-8 text-center">
          <p className="text-sm">The trace list could not be shown.</p>
          <div className="flex items-center justify-center gap-3">
            <Button size="sm" variant="outline" onClick={reset}>
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
