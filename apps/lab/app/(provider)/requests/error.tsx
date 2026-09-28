"use client";

import Link from "next/link";

/**
 * The last resort. Everything these pages expect to go wrong is a typed refusal the view renders, so
 * reaching this boundary means something threw. They are read-only: a fresh load of the first page is
 * the retry (a full navigation re-reads on the server). `error.message` is never shown.
 */
export default function RequestsError() {
  return (
    <>
      <h1>Requests</h1>
      <p role="alert">This page could not be shown.</p>
      <Link href="/requests">Start from the newest requests</Link>
    </>
  );
}
