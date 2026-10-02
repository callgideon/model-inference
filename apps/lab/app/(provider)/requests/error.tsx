"use client";

import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";

/**
 * The last resort. Everything these pages expect to go wrong is a typed refusal the view renders, so
 * reaching this boundary means something threw. They are read-only: a fresh load of the first page is
 * the retry (a full navigation re-reads on the server). `error.message` is never shown.
 */
export default function RequestsError() {
  return (
    <div className="lab-stack">
      <PageHeader title="Requests" />
      <ServiceState
        state="unavailable"
        title="This page could not be shown"
        explanation="Nothing is shown until the request records can be read."
        action={<Link href="/requests">Start from the newest requests</Link>}
      />
    </div>
  );
}
