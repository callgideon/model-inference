// UX-09 (L-10): the training page's own decisions, beside it (lib/services/pipelines/view.ts stays the P4
// rows). Type-only imports, so node --test runs it.
import type { TrainingRun } from "@/lib/services/pipelines/port";

/** P3 reconciles a submission whose outcome is unknown by lookup only (`submit` on an ambiguous or stuck
 * `submitting` run never submits again), so the page offers that lookup there - to a writer - and
 * nothing that reads as a resubmit. */
export const canLookUp = (state: TrainingRun["state"], writer: boolean): boolean => writer && (state === "ambiguous" || state === "submitting");

export const LOOKUP_LABEL = "Look up the outcome (never submits again)";

/** While the run and checkpoint listings cannot be read (SR-AP10-2 / AP-10e not composed, or the service
 * is down) the page offers no action on them; the teacher section has its own read. */
export const GATED_COPY =
  "Until the training run and checkpoint records can be read here, preparing a bundle and importing a checkpoint are not offered. Teacher labelling below is read separately.";
