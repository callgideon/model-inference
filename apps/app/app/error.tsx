"use client";
// I3: a failed segment shows a safe page (no message, no stack) and reports itself (lib/deploy/error-view.ts).
import { ErrorView, type ErrorProps } from "@/lib/deploy/error-view";

export default function Error(props: ErrorProps) {
  return <ErrorView {...props} />;
}
