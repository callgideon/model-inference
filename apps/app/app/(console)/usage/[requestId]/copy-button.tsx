"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { copyText } from "./request-view-model";

const SAID = { copied: "Copied to the clipboard.", failed: "Copying was blocked. Select the text and copy it by hand." };

/** Copies `text`; says whether it worked (UX-07: a blocked clipboard is never reported as copied). */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [outcome, setOutcome] = useState<keyof typeof SAID | null>(null);
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={() => copyText(text, navigator.clipboard).then(setOutcome)}>
        {label}
      </Button>
      <span role="status" className="text-xs text-muted-foreground">
        {outcome === null ? "" : SAID[outcome]}
      </span>
    </span>
  );
}
