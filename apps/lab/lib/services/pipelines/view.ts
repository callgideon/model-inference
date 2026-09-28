// P4: rows and copy derived only from the P1/P3 records. Nothing here remembers what a button did; an
// unknown submission, an unknown cost and a rejected checkpoint are said as such (WR-P-6).
import type { Role } from "../../auth/access.ts";
import { holds, IMPORT_REFUSALS, MANUAL, REFUSALS, type Checkpoint, type ImportReceipt, type Label, type LabelExport, type Refusal, type TrainingRun } from "./port.ts";

const KIND: Record<Label["method"], string> = {
  synthetic: "Synthetic (model-generated)",
  imported: "Imported (external human pipeline)",
  human: "Human review in this Lab",
};
export type LabelRow = { id: string; sample: string; kind: string; truth: string; state: string; value: string; reviewer: string; reviewable: boolean };

export function labelRows(role: Role, labels: Label[]): LabelRow[] {
  return labels.map((l) => ({
    id: l.annotationRef, sample: l.sampleId, kind: KIND[l.method],
    truth: l.method === "human" && l.groundTruth ? "ground truth (human review)" : "not ground truth",
    state: l.state, value: l.value, reviewer: l.reviewerId ?? "—",
    reviewable: l.state === "submitted" && holds(role, "run_evaluation"),
  }));
}

const IMPORT_COPY: Record<(typeof IMPORT_REFUSALS)[number], string> = {
  missing_evidence: "sample is not in this dataset",
  grant_not_current: "the sample's grant is not current",
  forged_ground_truth: "an import cannot claim ground truth",
  bad_mapping: "the row does not map to a label",
};
export function importRows(receipts: ImportReceipt[]) {
  return receipts.map((r) => ({
    id: r.importId, dataset: r.datasetRef, summary: `${r.accepted} accepted · ${r.rejected.length} rejected`,
    rejected: r.rejected.map((x) => `Row ${x.row}: ${(IMPORT_REFUSALS as readonly string[]).includes(x.reason) ? IMPORT_COPY[x.reason] : "rejected (reason not recognised)"}`),
  }));
}

export function exportRows(exports: LabelExport[]) {
  return exports.map((e) => ({
    id: e.exportId, dataset: e.datasetRef, adapter: e.adapter, items: e.items, expires: e.expiresAt,
    lineage: e.lineage.map((x) => `${x.sampleId} ← ${x.labelRefs.join(", ")} (${x.methods.join(", ")})`),
    omitted: e.omitted.map((x) => `${x.sampleId}: ${x.reason}`),
  }));
}

export type RunAction = "submit" | "finish" | "cancel";
const manual = (r: TrainingRun) => r.connector === MANUAL;

function stateCopy(r: TrainingRun): string {
  switch (r.state) {
    case "prepared": return "Prepared: the bundle is ready; nothing submitted.";
    case "submitting": return "Submitting: the outcome is being recorded.";
    case "submitted": return manual(r) ? "Training on your own compute; mark it finished when it is." : `Submitted to ${r.connector}.`;
    case "ambiguous": return `Submission outcome unknown: ${r.reservedUsd} USD held until it is looked up; it is never resubmitted.`;
    case "completed": return "Training finished.";
    case "failed": return `Failed: ${r.reason ?? "no reason recorded"}.`;
    case "cancelled": return "Cancelled.";
  }
}

function costCopy(r: TrainingRun): string {
  if (manual(r)) return "Nothing reserved or charged: the manual bundle trains on your own compute.";
  if (!r.settled) return "Not settled yet.";
  return r.costUsd === null ? "Unknown: the provider reported no cost. It is never estimated." : `${r.costUsd} USD, as reported.`;
}

export function runRows(role: Role, runs: TrainingRun[]) {
  return runs.map((r) => {
    const actions: RunAction[] = [];
    if (holds(role, "run_evaluation")) {
      if (r.state === "prepared") actions.push("submit");
      if (r.state === "submitted" && manual(r)) actions.push("finish");
      if (r.state === "prepared" || r.state === "submitted") actions.push("cancel");
    }
    return {
      id: r.externalRunId, run: r.runRef, connector: r.connector, state: stateCopy(r), cost: costCopy(r), actions,
      config: `${r.config.objective} · ${r.config.adaptation} · base ${r.config.baseModel}`,
      data: `${r.datasetRef} · ${r.train} train, ${r.dev} dev, ${r.omitted} omitted · export ${r.export.exportId}`,
      holdout: `${r.holdout.size} held-out samples, pinned ${r.holdout.sha256}`,
      budget: `limit ${r.limitUsd} USD · reserved ${r.reservedUsd} USD`, payer: r.payerRef,
    };
  });
}

const CHECKPOINT_COPY: Record<string, string> = {
  missing_artifact: "the artifact is missing",
  digest_mismatch: "the artifact's bytes do not match the declared digest",
  incompatible: "the artifact is for another base model or adaptation",
  rejected: "the receipt was rejected",
};
const rejection = (reason: string | null) => {
  const run = /^run_([a-z]+)$/.exec(reason ?? "");
  if (run) return `Rejected: it arrived while the run was ${run[1]}. Never evaluated.`;
  return reason !== null && Object.hasOwn(CHECKPOINT_COPY, reason) ? `Rejected: ${CHECKPOINT_COPY[reason]}. Never evaluated.` : "Rejected: reason not recognised.";
};

export function checkpointRows(role: Role, checkpoints: Checkpoint[], runs: TrainingRun[]) {
  return checkpoints.map((c) => {
    const r = runs.find((x) => x.externalRunId === c.externalRunId);
    const e = c.evaluation;
    const onHoldout = r !== undefined && e !== null && e.split === "holdout" && e.holdoutSha256 === r.holdout.sha256;
    const passed = onHoldout && e!.state === "succeeded";
    return {
      id: c.checkpointId, run: c.externalRunId, digest: c.artifactDigest,
      lineage: r === undefined ? `run ${c.externalRunId} is not in this workspace's records`
        : `${r.runRef} → ${r.datasetRef} · export ${r.export.exportId} · ${r.config.objective} · ${r.config.adaptation} · base ${r.config.baseModel} · holdout ${r.holdout.sha256}`,
      state: c.state === "rejected" ? rejection(c.reason)
        : c.eligible ? "Eligible candidate: not public and not promoted."
        : !onHoldout ? "Not evaluated on this run's pinned holdout: not eligible."
        : e!.state === "succeeded" ? "Held-out evaluation succeeded; awaiting approval."
        : `Held-out evaluation ${e!.state}.`,
      comparison: e !== null ? `/evaluations?run=${encodeURIComponent(e.runRef)}` : null,
      approvable: passed && !c.eligible && holds(role, "run_evaluation"),
    };
  });
}

export const REFUSAL_COPY: Record<Refusal, string> = {
  denied: "Your role in this workspace does not allow that, or a source's grant is no longer current.",
  not_found: "That record is not in this workspace.",
  invalid: "Those values were not accepted. Check the ids, the payer, the USD amount and the configuration.",
  conflict: "That record has moved on or was already written with other values; this page shows its current state.",
  gone: "That export has expired or was cancelled. Export again.",
  unavailable: "Pipeline records could not be read. Nothing is shown until they can be; try again shortly.",
};

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown. */
export function refusalCopy(value: unknown): string | null {
  return (REFUSALS as readonly unknown[]).includes(value) ? REFUSAL_COPY[value as Refusal] : null;
}
