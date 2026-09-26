"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { browserTimer, pollLoop } from "./request-view-model";

/**
 * Re-reads the page while the request is unfinished (or its read hit an outage): bounded
 * exponential backoff, stopped on navigation away (unmount) and after `MAX_POLLS`. It only
 * re-renders the server page — it never submits anything.
 */
export function StatusPoller() {
  const router = useRouter();
  const [stopped, setStopped] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => pollLoop(browserTimer, () => router.refresh(), () => setStopped(true)), [router, round]);

  return (
    <p role="status" className="mt-4 text-xs text-muted-foreground">
      {stopped ? (
        <>
          Stopped checking automatically.{" "}
          <button
            type="button"
            className="underline underline-offset-4"
            onClick={() => {
              setStopped(false);
              setRound((n) => n + 1);
              router.refresh();
            }}
          >
            Check again
          </button>
        </>
      ) : (
        "This page checks for updates while the request is unfinished."
      )}
    </p>
  );
}
