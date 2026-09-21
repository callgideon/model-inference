"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { boundaryCopy } from "./boundary";

/**
 * The route's error boundary. Every *expected* failure is a typed `Result` the page renders as an
 * error state; this catches the unexpected throw, so a bug renders something a person can act on
 * rather than a dead stream. It shows nothing from the error itself: a thrown error can carry
 * internals, and a Server Component error reaches the client as a digest anyway.
 *
 * `retry`, not `reset`: this page is a Server Component, so the throw happened on the server and
 * only a re-fetch can recover. Next 16.3.5's shipped docs say `reset()` re-renders the children
 * *without* re-fetching — it would replay the same errored payload — and that `retry()` is what to
 * use. See `./boundary.ts`.
 */
export default function UsageError({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  const copy = boundaryCopy("usage");
  return (
    <Card>
      <CardContent className="space-y-3 py-10 text-center">
        <p className="font-medium">{copy.headline}</p>
        <p className="text-sm text-muted-foreground">{copy.detail}</p>
        <Button variant="outline" size="sm" onClick={() => retry()}>
          {copy.action}
        </Button>
      </CardContent>
    </Card>
  );
}
