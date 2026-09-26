"use client";
/**
 * I3: the App's error page body, shared by app/error.tsx and app/global-error.tsx. It shows no
 * message and no stack — only a reference the operator can grep (the error digest) and the
 * release commit's short form — and reports once to /api/client-errors (lib/deploy/report.ts).
 * Written with createElement, not JSX, so `node --test` renders it (tests/i3).
 */
import { createElement as h, useEffect } from "react";
import { REPORT_PATH, browserReport, safeDigest, shortCommit } from "./report.ts";

export type ErrorProps = { error: Error & { digest?: string }; retry: () => void };

export function ErrorView({ error, retry }: ErrorProps) {
  useEffect(() => {
    fetch(REPORT_PATH, {
      method: "POST",
      keepalive: true,
      headers: { "content-type": "application/json" },
      body: JSON.stringify(browserReport(error, window.location.pathname)),
    }).catch(() => undefined); // reporting never becomes a second error
  }, [error]);
  const commit = shortCommit({
    VERCEL_GIT_COMMIT_SHA: process.env.VERCEL_GIT_COMMIT_SHA,
    INFRX_RELEASE_SHA: process.env.INFRX_RELEASE_SHA,
  });
  return h(
    "main",
    { className: "mx-auto flex min-h-svh max-w-md flex-col justify-center gap-4 p-6" },
    h("h1", { className: "text-xl font-semibold" }, "Something went wrong"),
    h(
      "p",
      { className: "text-sm text-muted-foreground" },
      "This page could not be shown. Try again; if it keeps failing, contact support and quote the reference below.",
    ),
    h("p", { className: "font-mono text-xs text-muted-foreground" }, `Reference ${safeDigest(error.digest) ?? "none"} · release ${commit}`),
    h(
      "button",
      { type: "button", onClick: () => retry(), className: "self-start rounded-md border px-3 py-1.5 text-sm" },
      "Try again",
    ),
  );
}
