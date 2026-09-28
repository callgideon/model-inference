// WR-C3L-2: the fixed copy for a C3L action's outcome. A refusal never renders server data; a
// calibration page reports its size and cursor, nothing else.
import type { Outcome } from "./core.ts";

const REFUSED = {
  denied: "Refused: this workspace may not do that.",
  invalid: "Check the fields: something is not in the expected form.",
  unavailable: "The judge service is unavailable. Nothing was changed.",
} as const;

export function outcomeText(outcome: Outcome): string {
  if (!outcome.ok) return REFUSED[outcome.reason];
  const page = outcome.data as { rows?: unknown; next?: unknown } | null;
  if (page && Array.isArray(page.rows)) {
    const more = typeof page.next === "string" ? ` (more after ${page.next})` : "";
    return `Done: ${page.rows.length} calibration labels${more}.`;
  }
  return "Done.";
}
