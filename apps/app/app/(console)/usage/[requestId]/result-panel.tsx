"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { CopyButton } from "./copy-button";
import { browserTimer, readResult, watchExpiry, watchResult, type Shown } from "./request-view-model";

const MESSAGES: Record<Exclude<Shown["state"], "ready" | "signed_out">, string> = {
  loading: "Loading the result…",
  pending: "The result is not ready yet.",
  withheld: "This result is not served while the request's usage awaits reconciliation.",
  no_result: "This request did not produce a result.",
  not_found: "We could not find a result for this request in your account.",
  // Read expiry is not physical deletion (UX-01): say what the reader can and cannot do.
  expired: "This result is no longer available. Request status and usage remain available.",
  unavailable: "The result could not be loaded right now.",
};

/**
 * The owned result's content (U4). Held only in this component's memory: fetched from the no-store
 * result route on mount and on a back/forward restore, dropped at the persisted expiry, and never
 * written to browser storage, logs or analytics. Every button re-reads; none reruns the request.
 */
export function ResultPanel({ requestId, expiresAt }: { requestId: string; expiresAt: string }) {
  const [shown, setShown] = useState<Shown>({ state: "loading" });

  // Read on mount and again on a back/forward restore; abort on navigation away.
  useEffect(() => watchResult((signal) => readResult(requestId, signal), setShown, window), [requestId]);

  // Drop the content at the persisted expiry.
  useEffect(() => {
    if (shown.state !== "ready") return;
    return watchExpiry(expiresAt, Date.now, browserTimer, () => setShown({ state: "expired" }));
  }, [shown.state, expiresAt]);

  if (shown.state === "ready") {
    const text = shown.text;
    const download = () => {
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${requestId}.txt`;
      link.click();
      URL.revokeObjectURL(url);
    };
    return (
      <div className="space-y-3">
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 font-mono text-sm">
          {text}
        </pre>
        <div className="flex flex-wrap items-center gap-2">
          <CopyButton text={text} label="Copy result" />
          <Button variant="outline" size="sm" onClick={download}>
            Download
          </Button>
        </div>
      </div>
    );
  }

  if (shown.state === "signed_out") {
    return (
      <p role="status" className="text-sm">
        Your session ended.{" "}
        <Link className="underline underline-offset-4" href={`/login?next=${encodeURIComponent(`/usage/${requestId}`)}`}>
          Sign in again
        </Link>{" "}
        to see this result.
      </p>
    );
  }

  return (
    <div role="status" className="space-y-2 text-sm text-muted-foreground">
      <p>{MESSAGES[shown.state]}</p>
      {shown.state === "unavailable" ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setShown({ state: "loading" });
            readResult(requestId).then((read) => {
              if (read !== null) setShown(read);
            });
          }}
        >
          Retry loading result
        </Button>
      ) : null}
    </div>
  );
}
