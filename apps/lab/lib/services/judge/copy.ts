// WR-C3L-2: the fixed copy for a C3L action's outcome. A refusal never renders server data; a
// calibration reports the route's state and label counts, nothing else.
import type { Outcome } from "./core.ts";

const REFUSED = {
  denied: "Refused: this workspace may not do that.",
  invalid: "Check the fields: something is not in the expected form.",
  conflict: "Already submitted with different values. Reload the page and try again.",
  unavailable: "The judge service is unavailable. Nothing was changed.",
} as const;
const STATES = ["calibrated", "insufficient", "uncalibrated"];

export function outcomeText(outcome: Outcome): string {
  if (!outcome.ok) return REFUSED[outcome.reason];
  const c = outcome.data as { state?: unknown; labels?: unknown; required?: unknown } | null;
  if (c && STATES.includes(c.state as string) && Number.isInteger(c.labels) && Number.isInteger(c.required)) {
    return `Calibration: ${c.state}, ${c.labels} of ${c.required} reviewed labels.`;
  }
  return "Done.";
}
