"use client";
// I3: the root layout itself failed; this replaces it, so it brings its own html, body and styles.
import { ErrorView, type ErrorProps } from "@/lib/deploy/error-view";
import "./globals.css";

export default function GlobalError(props: ErrorProps) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-svh bg-background text-foreground antialiased">
        <ErrorView {...props} />
      </body>
    </html>
  );
}
