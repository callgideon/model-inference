"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { buttonClass } from "./button";

/**
 * Copies `value`. "Copied" only once the clipboard write resolves; a failure (refused permission,
 * insecure context) says so and shows the value as selectable text. `label` names what is copied.
 */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = () => {
    setState("idle");
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(value))
      .then(
        () => setState("copied"),
        () => setState("failed"),
      );
  };
  return (
    <span className="lab-copy">
      <button type="button" className={buttonClass("ghost", "sm")} aria-label={label} onClick={copy}>
        {state === "copied" ? <Check aria-hidden /> : <Copy aria-hidden />}
      </button>
      <span role="status" className="lab-copy__status">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed. Select the text to copy it." : ""}
      </span>
      {state === "failed" ? <code className="lab-copy__fallback">{value}</code> : null}
    </span>
  );
}
