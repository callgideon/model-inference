"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { clientResultRead, expiryDelayMs, type ResultRead } from "./request-view-model";

type Shown = ResultRead | { state: "loading" };

const MESSAGES: Record<Exclude<Shown["state"], "ready" | "signed_out">, string> = {
  loading: "Loading the result…",
  pending: "The result is not ready yet.",
  withheld: "This result is not served while the request's usage awaits reconciliation.",
  no_result: "This request did not produce a result.",
  not_found: "We could not find a result for this request in your account.",
  expired: "The result expired and its content was removed. The request's details and charge stay on this page.",
  unavailable: "The result could not be loaded right now.",
};

/** One read of the no-store result route; `null` when the read was abandoned (navigation). */
async function readResult(requestId: string, signal?: AbortSignal): Promise<ResultRead | null> {
  try {
    const response = await fetch(`/usage/${encodeURIComponent(requestId)}/result`, {
      cache: "no-store",
      credentials: "same-origin",
      signal,
    });
    const json = (response.headers.get("content-type") ?? "").startsWith("application/json");
    const body: unknown = json ? await response.json() : null;
    return clientResultRead({ status: response.status, redirected: response.redirected, json }, body);
  } catch {
    return signal?.aborted ? null : { state: "unavailable" };
  }
}

/**
 * The owned result's content (U4). Held only in this component's memory: fetched from the no-store
 * result route on mount and on a back/forward restore, dropped at the persisted expiry, and never
 * written to browser storage, logs or analytics. Every button re-reads; none reruns the request.
 */
export function ResultPanel({ requestId, expiresAt }: { requestId: string; expiresAt: string }) {
  const [shown, setShown] = useState<Shown>({ state: "loading" });
  const [copied, setCopied] = useState(false);

  // Read on mount; stop on navigation away. A page restored from the back/forward cache reads
  // again, so content that expired meanwhile is not shown from memory.
  useEffect(() => {
    const controller = new AbortController();
    const show = (read: ResultRead | null) => {
      if (read !== null) setShown(read);
    };
    readResult(requestId, controller.signal).then(show);
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) readResult(requestId, controller.signal).then(show);
    };
    window.addEventListener("pageshow", onShow);
    return () => {
      controller.abort();
      window.removeEventListener("pageshow", onShow);
    };
  }, [requestId]);

  // Drop the content at the persisted expiry (re-armed past setTimeout's ceiling).
  useEffect(() => {
    if (shown.state !== "ready") return;
    let timer: ReturnType<typeof setTimeout>;
    const arm = () => {
      timer = setTimeout(() => {
        if (expiryDelayMs(expiresAt, Date.now()) === 0) setShown({ state: "expired" });
        else arm();
      }, expiryDelayMs(expiresAt, Date.now()));
    };
    arm();
    return () => clearTimeout(timer);
  }, [shown.state, expiresAt]);

  if (shown.state === "ready") {
    const text = shown.text;
    const copy = () => {
      navigator.clipboard.writeText(text).then(
        () => setCopied(true),
        () => setCopied(false),
      );
    };
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
          <Button variant="outline" size="sm" onClick={copy}>
            Copy
          </Button>
          <Button variant="outline" size="sm" onClick={download}>
            Download
          </Button>
          <span role="status" className="text-xs text-muted-foreground">
            {copied ? "Copied to the clipboard." : ""}
          </span>
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
          Load the result again
        </Button>
      ) : null}
    </div>
  );
}
