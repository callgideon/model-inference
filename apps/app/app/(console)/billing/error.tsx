"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The route's error boundary. Every *expected* failure is a typed `Result` the page renders as an
 * error state; this catches the unexpected throw, so a bug renders something a person can act on
 * rather than a dead stream. It deliberately shows no message from the error: a thrown error can
 * carry anything, including internals.
 */
export default function BillingError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <Card>
      <CardContent className="space-y-3 py-10 text-center">
        <p className="font-medium">This page could not be displayed</p>
        <p className="text-sm text-muted-foreground">
          Something went wrong while rendering your balance. Your ledger and your grants are
          unaffected — nothing here changes accounting.
        </p>
        <Button variant="outline" size="sm" onClick={reset}>
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}
